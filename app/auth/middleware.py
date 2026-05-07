"""FastAPI middleware that verifies the Supabase JWT and exposes the user.

Anonymous routes (paths in `ANON_PATHS`) bypass entirely. Every other
request must carry `Authorization: Bearer <jwt>`; on success
`request.state.user` is populated with `id`, `email`, and the app `role`
loaded from `users_profile`.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware

from app.auth.jwt_verify import AuthError, verify_token
from app.db.engine import get_session_factory
from app.db.models import UserProfile

# Paths that do not require auth.
ANON_PATHS: tuple[str, ...] = (
    "/",
    "/health",
    "/openapi.json",
    "/docs",
    "/redoc",
    "/docs/oauth2-redirect",
    "/auth/login",
    "/auth/refresh",
)
ANON_PREFIXES: tuple[str, ...] = ("/static/",)


@dataclass(frozen=True)
class AuthUser:
    id: str
    email: str
    role: str
    display_name: str | None
    organization: str | None


def _is_anonymous(path: str) -> bool:
    if path in ANON_PATHS:
        return True
    return any(path.startswith(p) for p in ANON_PREFIXES)


async def _load_profile(user_id: str, email: str) -> AuthUser | None:
    async with get_session_factory()() as session:
        row = await session.execute(
            select(UserProfile).where(UserProfile.id == user_id)
        )
        prof = row.scalar_one_or_none()
        if prof is None:
            return None
        return AuthUser(
            id=str(prof.id),
            email=prof.email or email,
            role=prof.role,
            display_name=prof.display_name,
            organization=prof.organization,
        )


class SupabaseAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # CORS preflights must never be authenticated — let CORSMiddleware
        # answer them. Without this, browsers see 401 on OPTIONS and never
        # issue the real request.
        if request.method == "OPTIONS":
            return await call_next(request)
        if _is_anonymous(request.url.path):
            return await call_next(request)

        header = request.headers.get("authorization") or ""
        if not header.lower().startswith("bearer "):
            return JSONResponse(
                {"detail": "missing_bearer_token", "error_code": "unauthorized"},
                status_code=401,
            )
        token = header.split(" ", 1)[1].strip()
        try:
            claims = verify_token(token)
        except AuthError as exc:
            return JSONResponse(
                {"detail": str(exc), "error_code": "invalid_token"},
                status_code=401,
            )

        user = await _load_profile(claims.sub, claims.email)
        if user is None:
            return JSONResponse(
                {"detail": "profile_not_found", "error_code": "no_profile"},
                status_code=403,
            )

        request.state.user = user
        return await call_next(request)
