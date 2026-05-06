"""End-to-end smoke test for PR-5 (Supabase auth + /auth/me).

Steps:
  1. Admin-create a test user via service-role (email_confirm=True).
  2. Verify the trigger inserted into public.users_profile.
  3. Promote that user to super_admin.
  4. Sign in with anon key + email/password to get an access_token.
  5. Call GET /auth/me on http://localhost:8080 with the bearer token.
  6. Tear the user down (admin delete \u2192 cascade removes profile).

Run:  python -m app.tools.e2e_auth
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

EMAIL = "e2e@wellsynth.local"
APP_URL = os.getenv("APP_URL", "http://localhost:8080")


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


async def _profile_row(dsn: str, user_id: str) -> dict | None:
    conn = await asyncpg.connect(_to_asyncpg_dsn(dsn))
    try:
        row = await conn.fetchrow(
            "select id::text, email, role, display_name from public.users_profile where id = $1",
            user_id,
        )
        return dict(row) if row else None
    finally:
        await conn.close()


async def main() -> int:
    load_dotenv()
    url = os.environ["SUPABASE_URL"]
    anon = os.environ["SUPABASE_ANON_KEY"]
    service = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    dsn = os.environ["DATABASE_URL"]

    password = "E2E-" + secrets.token_urlsafe(24)

    admin = create_client(url, service)
    client = create_client(url, anon)

    # 1. Create user (auto-confirmed)
    print(f"[1] admin.create_user {EMAIL}")
    created = admin.auth.admin.create_user(
        {"email": EMAIL, "password": password, "email_confirm": True}
    )
    user_id = created.user.id
    print(f"    user_id = {user_id}")

    try:
        # 2. Profile row from trigger
        prof = await _profile_row(dsn, user_id)
        assert prof is not None, "users_profile row missing \u2014 trigger broken"
        print(f"[2] profile present: role={prof['role']!r}")

        # 3. Promote
        await _set_role(dsn, user_id, "super_admin")
        print("[3] promoted to super_admin")

        # 4. Sign in
        signed_in = client.auth.sign_in_with_password(
            {"email": EMAIL, "password": password}
        )
        token = signed_in.session.access_token
        print(f"[4] got access_token (len={len(token)})")

        # 5. /auth/me
        async with httpx.AsyncClient(base_url=APP_URL, timeout=10.0) as http:
            r = await http.get(
                "/auth/me", headers={"Authorization": f"Bearer {token}"}
            )
        print(f"[5] GET /auth/me -> {r.status_code} {r.json()}")
        assert r.status_code == 200, f"expected 200, got {r.status_code}"
        body = r.json()
        assert body["id"] == user_id
        assert body["email"] == EMAIL
        assert body["role"] == "super_admin"
        print("    OK")

        # bonus: protected route without token should be 401
        async with httpx.AsyncClient(base_url=APP_URL, timeout=10.0) as http:
            r2 = await http.get("/auth/me")
        assert r2.status_code == 401
        print(f"[5b] GET /auth/me (no token) -> {r2.status_code}  OK")

        return 0
    finally:
        # 6. Cleanup
        try:
            admin.auth.admin.delete_user(user_id)
            print("[6] deleted test user")
        except Exception as exc:
            print(f"[6] cleanup failed: {exc}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
