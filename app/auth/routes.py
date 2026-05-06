"""Auth-related endpoints.

Per ROLES + Supabase pivot we do NOT implement /auth/signup, /auth/login,
or password reset on the backend — the frontend uses @supabase/supabase-js
directly. The only endpoint here is GET /auth/me, which returns the
profile loaded by SupabaseAuthMiddleware.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me", summary="Current user profile")
async def me(request: Request) -> dict:
    user = request.state.user
    return {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "display_name": user.display_name,
    }
