"""Supabase JWT verification.

Supabase Auth issues asymmetric (ES256 / P-256) access tokens. The public
keys are published at the project's JWKS endpoint. We fetch + cache them
once at process start and refresh on `kid` miss.
"""

from __future__ import annotations

from dataclasses import dataclass

import jwt
from jwt import PyJWKClient

from app.config import get_settings

_jwks_client: PyJWKClient | None = None


def _client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        settings = get_settings()
        if not settings.supabase_jwks_url:
            raise RuntimeError("SUPABASE_JWKS_URL is not set")
        _jwks_client = PyJWKClient(settings.supabase_jwks_url, cache_keys=True)
    return _jwks_client


@dataclass(frozen=True)
class TokenClaims:
    sub: str          # auth.users.id (uuid string)
    email: str
    role: str | None  # supabase auth role ('authenticated' usually); not the app role


class AuthError(Exception):
    """Raised on any token verification failure. Mapped to 401 by middleware."""


def verify_token(token: str) -> TokenClaims:
    """Verify an ES256 JWT against the Supabase JWKS and return its claims."""
    try:
        signing_key = _client().get_signing_key_from_jwt(token).key
        payload = jwt.decode(
            token,
            signing_key,
            algorithms=["ES256"],
            audience="authenticated",
            leeway=60,  # tolerate small clock skew between local host and Supabase
            options={"require": ["exp", "sub", "email"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthError(str(exc)) from exc

    return TokenClaims(
        sub=payload["sub"],
        email=payload["email"],
        role=payload.get("role"),
    )
