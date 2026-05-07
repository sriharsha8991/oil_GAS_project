"""Bootstrap a super_admin account.

Run once per environment, before any normal use of the app. Creates a
Supabase auth.users row with the given email + password, then flips the
auto-created users_profile row's role to 'super_admin'. Idempotent: if
the email already exists, only the role is upgraded.

Usage:
    python -m app.tools.bootstrap_super_admin \\
        --email me@example.com --password 'somethingStrong'

If --password is omitted, a random one is generated and printed once.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys

import asyncpg
from dotenv import load_dotenv
from supabase import create_client


def _to_asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _promote(dsn: str, user_id: str) -> None:
    conn = await asyncpg.connect(_to_asyncpg_dsn(dsn))
    try:
        await conn.execute(
            "update public.users_profile set role = 'super_admin' where id = $1",
            user_id,
        )
    finally:
        await conn.close()


async def _find_user_id_by_email(dsn: str, email: str) -> str | None:
    conn = await asyncpg.connect(_to_asyncpg_dsn(dsn))
    try:
        row = await conn.fetchrow(
            "select id from auth.users where lower(email) = lower($1)", email
        )
        return str(row["id"]) if row else None
    finally:
        await conn.close()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", default=None)
    parser.add_argument("--display-name", default=None)
    args = parser.parse_args()

    load_dotenv()
    url = os.environ["SUPABASE_URL"]
    service = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    dsn = os.environ["DATABASE_URL"]

    admin = create_client(url, service)
    email = args.email.strip().lower()

    existing_id = await _find_user_id_by_email(dsn, email)
    if existing_id:
        await _promote(dsn, existing_id)
        print(f"[ok] {email} already existed; promoted to super_admin (id={existing_id})")
        return 0

    password = args.password or ("Wsx-" + secrets.token_urlsafe(18))
    metadata = {"display_name": args.display_name} if args.display_name else {}
    created = admin.auth.admin.create_user(
        {
            "email": email,
            "password": password,
            "email_confirm": True,
            "user_metadata": metadata,
        }
    )
    user_id = created.user.id

    # handle_new_user trigger created the profile; flip its role.
    for _ in range(10):
        try:
            await _promote(dsn, user_id)
            break
        except Exception:
            await asyncio.sleep(0.2)

    print(f"[ok] created super_admin id={user_id}")
    print(f"     email:    {email}")
    if not args.password:
        print(f"     password: {password}   (only shown once)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
