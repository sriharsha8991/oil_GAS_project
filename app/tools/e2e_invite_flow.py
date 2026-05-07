"""End-to-end smoke for the invite-only auth model.

Flow:
  1. Bootstrap a super_admin (idempotent).
  2. Sign in as super_admin via /auth/login (HTTP only — no Supabase JS).
  3. POST /admin/users/invite with role='admin' + organization='Acme' + user_quota=2.
  4. (Out-of-band) read the temp password for the new admin from Postgres-side
     test fixture: we set it ourselves via service-role then immediately use
     /auth/login. (Skips the real email round-trip — that's a separate manual test.)
  5. Sign in as that admin.
  6. Admin invites user_a (geologist) — expect 201.
  7. Admin invites user_b (drilling_engineer) — expect 201.
  8. Admin invites user_c — expect 403 quota_exceeded.
  9. Admin tries to invite role='admin' — expect 403 role_not_assignable_by_admin.
 10. user_a signs in with their temp password, hits /auth/me, calls /auth/change-password,
     re-signs-in with the new password. (Same trick: we set their pw via service-role.)
 11. super_admin PATCHes admin's quota to 3, admin invites user_c successfully.
 12. Cleanup all.

The Resend send is bypassed in steps 3/6/7/8 by overriding the password
server-side after invite (we accept the email-send failure rolls back; so
we instead create users via service-role + set the role/org/quota by
writing users_profile directly, mirroring what /admin/users/invite does
once the email succeeds).

Run: python -m app.tools.e2e_invite_flow
"""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
import uuid

import asyncpg
import httpx
from dotenv import load_dotenv
from supabase import create_client


APP_URL = os.getenv("APP_URL", "http://127.0.0.1:8080")
SUPER_EMAIL = os.getenv("E2E_SUPER_EMAIL", "e2e-super@example.com")
ADMIN_EMAIL = os.getenv("E2E_ADMIN_EMAIL", "e2e-org-admin@example.com")
USER_A_EMAIL = os.getenv("E2E_USER_A_EMAIL", "e2e-user-a@example.com")
USER_B_EMAIL = os.getenv("E2E_USER_B_EMAIL", "e2e-user-b@example.com")
USER_C_EMAIL = os.getenv("E2E_USER_C_EMAIL", "e2e-user-c@example.com")
ORG = "Acme Drilling"


def _to_asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _exec(dsn: str, sql: str, *args) -> None:
    conn = await asyncpg.connect(_to_asyncpg_dsn(dsn))
    try:
        await conn.execute(sql, *args)
    finally:
        await conn.close()


async def _fetchval(dsn: str, sql: str, *args):
    conn = await asyncpg.connect(_to_asyncpg_dsn(dsn))
    try:
        return await conn.fetchval(sql, *args)
    finally:
        await conn.close()


async def _delete_email(admin_sb, dsn: str, email: str) -> None:
    uid = await _fetchval(
        dsn, "select id from auth.users where lower(email) = lower($1)", email
    )
    if uid:
        try:
            admin_sb.auth.admin.delete_user(str(uid))
        except Exception:
            pass


def _create_user(admin_sb, email: str, password: str) -> str:
    res = admin_sb.auth.admin.create_user(
        {"email": email, "password": password, "email_confirm": True}
    )
    return res.user.id


async def _stamp_profile(
    dsn: str,
    user_id: str,
    *,
    role: str,
    organization: str | None = None,
    user_quota: int = 0,
    invited_by: str | None = None,
) -> None:
    # Wait for handle_new_user trigger.
    for _ in range(20):
        ok = await _fetchval(
            dsn, "select 1 from public.users_profile where id = $1", uuid.UUID(user_id)
        )
        if ok:
            break
        await asyncio.sleep(0.1)
    await _exec(
        dsn,
        """
        update public.users_profile
           set role = $1,
               organization = $2,
               user_quota = $3,
               invited_by = $4
         where id = $5
        """,
        role,
        organization,
        user_quota,
        uuid.UUID(invited_by) if invited_by else None,
        uuid.UUID(user_id),
    )


def _login(http: httpx.Client, email: str, password: str) -> str:
    r = http.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"login failed for {email}: {r.status_code} {r.text}"
    return r.json()["access_token"]


async def main() -> int:
    load_dotenv()
    url = os.environ["SUPABASE_URL"]
    service = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    dsn = os.environ["DATABASE_URL"]
    admin_sb = create_client(url, service)

    super_pw = "Sup-" + secrets.token_urlsafe(18)
    admin_pw = "Adm-" + secrets.token_urlsafe(18)

    # Clean slate for the test emails.
    for em in (SUPER_EMAIL, ADMIN_EMAIL, USER_A_EMAIL, USER_B_EMAIL, USER_C_EMAIL):
        await _delete_email(admin_sb, dsn, em)

    # 1. super_admin
    super_id = _create_user(admin_sb, SUPER_EMAIL, super_pw)
    await _stamp_profile(dsn, super_id, role="super_admin")
    print(f"[1] super_admin created id={super_id}")

    # 2. org admin (we create directly since invite flow tries to email; mirrors success path)
    admin_id = _create_user(admin_sb, ADMIN_EMAIL, admin_pw)
    await _stamp_profile(
        dsn, admin_id, role="admin", organization=ORG, user_quota=2, invited_by=super_id
    )
    print(f"[2] admin created id={admin_id} quota=2 org='{ORG}'")

    user_a_id: str | None = None
    user_b_id: str | None = None
    user_c_id: str | None = None
    a_pw = "Usra-" + secrets.token_urlsafe(18)
    b_pw = "Usrb-" + secrets.token_urlsafe(18)

    cleanup_ids: list[str] = [super_id, admin_id]

    try:
        with httpx.Client(base_url=APP_URL, timeout=15.0) as http:
            # 3. login super_admin
            super_tok = _login(http, SUPER_EMAIL, super_pw)
            print(f"[3] super_admin login OK (token len={len(super_tok)})")

            # 4. login org admin
            admin_tok = _login(http, ADMIN_EMAIL, admin_pw)
            print(f"[4] admin login OK")

            # 5. admin invites user_a — should succeed (we bypass email by pre-creating then asserting list visibility)
            #    NB: the real /admin/users/invite path calls Resend. We want to test contract,
            #    so we instead verify quota math + role gating using direct creates that mirror what
            #    a successful invite would persist. Then we hit /admin/users to confirm visibility.
            user_a_id = _create_user(admin_sb, USER_A_EMAIL, a_pw)
            await _stamp_profile(
                dsn, user_a_id, role="geologist", organization=ORG, invited_by=admin_id
            )
            cleanup_ids.append(user_a_id)
            print(f"[5] (simulated) admin invited {USER_A_EMAIL}")

            user_b_id = _create_user(admin_sb, USER_B_EMAIL, b_pw)
            await _stamp_profile(
                dsn,
                user_b_id,
                role="drilling_engineer",
                organization=ORG,
                invited_by=admin_id,
            )
            cleanup_ids.append(user_b_id)
            print(f"[6] (simulated) admin invited {USER_B_EMAIL}")

            # 7. quota check: real invite of user_c via API should now hit quota_exceeded
            r = http.post(
                "/admin/users/invite",
                json={"email": USER_C_EMAIL, "role": "geologist"},
                headers={"Authorization": f"Bearer {admin_tok}"},
            )
            assert r.status_code == 403, f"expected 403 quota, got {r.status_code} {r.text}"
            detail = r.json()["detail"]
            assert detail.get("error_code") == "quota_exceeded", detail
            print(f"[7] quota_exceeded enforced (user_quota={detail['user_quota']}, current={detail['current_invitees']})")

            # 8. admin tries to invite role=admin -> 403 role_not_assignable_by_admin
            r = http.post(
                "/admin/users/invite",
                json={"email": "x-shouldfail@example.com", "role": "admin"},
                headers={"Authorization": f"Bearer {admin_tok}"},
            )
            assert r.status_code == 403, r.text
            assert r.json()["detail"]["error_code"] == "role_not_assignable_by_admin"
            print(f"[8] admin cannot self-elevate invitees to 'admin'")

            # 9. user_a signs in with their temp password
            user_a_tok = _login(http, USER_A_EMAIL, a_pw)
            r = http.get("/auth/me", headers={"Authorization": f"Bearer {user_a_tok}"})
            assert r.status_code == 200
            me = r.json()
            assert me["role"] == "geologist" and me["organization"] == ORG
            print(f"[9] user_a /auth/me -> role=geologist org='{me['organization']}'")

            # 10. user_a changes password
            new_a_pw = "Newa-" + secrets.token_urlsafe(18)
            r = http.post(
                "/auth/change-password",
                json={"current_password": a_pw, "new_password": new_a_pw},
                headers={"Authorization": f"Bearer {user_a_tok}"},
            )
            assert r.status_code == 204, f"change-password: {r.status_code} {r.text}"
            # re-login with new pw
            _login(http, USER_A_EMAIL, new_a_pw)
            print(f"[10] user_a changed password and re-signed in")

            # 11. super_admin bumps admin quota to 3, admin's next invite quota check passes
            r = http.patch(
                f"/admin/users/{admin_id}/quota",
                json={"user_quota": 3},
                headers={"Authorization": f"Bearer {super_tok}"},
            )
            assert r.status_code == 200 and r.json()["user_quota"] == 3
            print(f"[11] super_admin set admin.user_quota=3")

            # Now admin's quota check is 2 < 3 → invite would proceed past quota gate.
            # We test via list visibility instead of triggering Resend.
            r = http.get(
                "/admin/users",
                params={"q": "user-a"},
                headers={"Authorization": f"Bearer {admin_tok}"},
            )
            assert r.status_code == 200
            items = r.json()["items"]
            assert any(u["id"] == user_a_id for u in items), "admin should see user_a in own org"
            assert all(u["organization"] == ORG for u in items)
            print(f"[12] admin GET /admin/users is org-scoped ({len(items)} item(s))")

            # 13. admin should NOT see super_admin's row
            r = http.get(
                "/admin/users",
                params={"q": "super"},
                headers={"Authorization": f"Bearer {admin_tok}"},
            )
            assert r.status_code == 200
            assert all(u["id"] != super_id for u in r.json()["items"])
            print(f"[13] admin cannot see super_admin (cross-org isolation)")

            # 14. admin tries cross-org delete (delete super_admin) -> 403
            r = http.delete(
                f"/admin/users/{super_id}",
                headers={"Authorization": f"Bearer {admin_tok}"},
            )
            assert r.status_code == 403, r.text
            print(f"[14] admin DELETE on super_admin -> 403")

            # 15. super_admin lists everyone
            r = http.get("/admin/users", headers={"Authorization": f"Bearer {super_tok}"})
            assert r.status_code == 200
            assert r.json()["total"] >= 4
            print(f"[15] super_admin sees all ({r.json()['total']} users)")

        return 0
    finally:
        for uid in cleanup_ids:
            try:
                admin_sb.auth.admin.delete_user(uid)
            except Exception:
                pass
        print("[cleanup] removed test users")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
