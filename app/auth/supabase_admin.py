"""Lazy singleton for the Supabase admin (service-role) client.

The service-role key bypasses RLS and exposes auth.admin.* APIs. It must
NEVER be returned to the browser. Only used from backend admin routes
gated by `require_role('super_admin')`.
"""

from __future__ import annotations

from supabase import Client, create_client

from app.config import get_settings

_client: Client | None = None


def get_admin_client() -> Client:
    global _client
    if _client is None:
        settings = get_settings()
        if not settings.supabase_url or not settings.supabase_service_role_key:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set"
            )
        _client = create_client(
            settings.supabase_url, settings.supabase_service_role_key
        )
    return _client
