"""End-to-end smoke for PR-5.1 user-management endpoints.

Steps:
  1. Admin-create a super_admin user (via service-role) to drive the API.
  2. Sign in as that admin to get a real JWT.
  3. PATCH /auth/me/profile (display_name update).
  4. POST /admin/users/invite (creates a 2nd test user via Supabase invite).
  5. GET /admin/users (search by email).
  6. PATCH /admin/users/{id}/role (promote -> geologist).
  7. DELETE /admin/users/{id} (the invitee).
  8. Cleanup: delete the admin user too.

Run:  python -m app.tools.e2e_admin
"""

from __future__ import annotations

import asyncio
import os
import secrets
import sys

import asyncpg
import httpx
from dotenv import load_dotenv
from supabase import create_client

ADMIN_EMAIL = os.getenv("E2E_ADMIN_EMAIL", "e2e-admin@example.com")
INVITEE_EMAIL = os.getenv("E2E_INVITEE_EMAIL", "srih8991@gmail.com")
APP_URL = os.getenv("APP_URL", "http://127.0.0.1:8080")


def _to_asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _set_role(dsn: str, user_id: str, role: str) -> None:
    conn = await asyncpg.connect(_to_asyncpg_dsn(dsn))
    try:
        await conn.execute(
            "update public.users_profile set role = $1 where id = $2",
            role,
            user_id,
        )
    finally:
        await conn.close()


async def main() -> int:
    load_dotenv()
    url = os.environ["SUPABASE_URL"]
    anon = os.environ["SUPABASE_ANON_KEY"]
    service = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    dsn = os.environ["DATABASE_URL"]

    admin_pw = "Admn-" + secrets.token_urlsafe(24)
    admin_sb = create_client(url, service)
    anon_sb = create_client(url, anon)

    # 1. Create admin user
    print(f"[1] create admin {ADMIN_EMAIL}")
    created = admin_sb.auth.admin.create_user(
        {"email": ADMIN_EMAIL, "password": admin_pw, "email_confirm": True}
    )
    admin_id = created.user.id
    await _set_role(dsn, admin_id, "super_admin")
    print(f"    promoted to super_admin (id={admin_id})")

    invitee_id: str | None = None
    try:
        # 2. Sign in
        signed = anon_sb.auth.sign_in_with_password(
            {"email": ADMIN_EMAIL, "password": admin_pw}
        )
        token = signed.session.access_token
        h = {"Authorization": f"Bearer {token}"}
        print(f"[2] signed in (token len={len(token)})")

        async with httpx.AsyncClient(base_url=APP_URL, timeout=15.0) as http:
            # 3. PATCH /auth/me/profile
            r = await http.patch(
                "/auth/me/profile", json={"display_name": "E2E Admin"}, headers=h
            )
            assert r.status_code == 200, r.text
            assert r.json()["display_name"] == "E2E Admin"
            print(f"[3] PATCH /auth/me/profile -> 200 display_name='E2E Admin'")

            # 4. Invite (Supabase Auth SMTP routes through Resend; rate limit
            #    is set in the Supabase dashboard).
            r = await http.post(
                "/admin/users/invite",
                json={
                    "email": INVITEE_EMAIL,
                    "display_name": "Invitee One",
                    "role": "geologist",
                },
                headers=h,
            )
            assert r.status_code == 201, f"invite failed: {r.status_code} {r.text}"
            body = r.json()
            invitee_id = body["id"]
            assert body["email"] == INVITEE_EMAIL
            assert body["role"] == "geologist"
            print(f"[4] POST /admin/users/invite -> 201 id={invitee_id} role={body['role']}")

            # 5. List with q
            r = await http.get(
                "/admin/users", params={"q": "invitee", "limit": 10}, headers=h
            )
            assert r.status_code == 200, r.text
            items = r.json()["items"]
            assert any(u["id"] == invitee_id for u in items), "invitee not in list"
            print(f"[5] GET /admin/users?q=invitee -> {len(items)} item(s)")

            # 6. PATCH role
            r = await http.patch(
                f"/admin/users/{invitee_id}/role",
                json={"role": "asset_manager"},
                headers=h,
            )
            assert r.status_code == 200, r.text
            assert r.json()["role"] == "asset_manager"
            print(f"[6] PATCH /admin/users/{{id}}/role -> 200 role=asset_manager")

            # 6b. Forbidden when caller is not super_admin
            await _set_role(dsn, admin_id, "drilling_engineer")
            r = await http.get("/admin/users", headers=h)
            assert r.status_code == 403, f"expected 403, got {r.status_code} {r.text}"
            print(f"[6b] non-admin GET /admin/users -> 403 OK")
            await _set_role(dsn, admin_id, "super_admin")

            # 7. DELETE invitee
            r = await http.delete(f"/admin/users/{invitee_id}", headers=h)
            assert r.status_code == 204, r.text
            print(f"[7] DELETE /admin/users/{{id}} -> 204")
            invitee_id = None

        return 0
    finally:
        # 8. cleanup
        if invitee_id:
            try:
                admin_sb.auth.admin.delete_user(invitee_id)
            except Exception:
                pass
        try:
            admin_sb.auth.admin.delete_user(admin_id)
            print("[8] deleted admin user")
        except Exception as exc:
            print(f"[8] cleanup failed: {exc}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
