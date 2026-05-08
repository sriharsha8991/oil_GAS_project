"""Auth-related endpoints.

Backend owns the entire credential surface so the frontend never imports
the Supabase JS SDK. Internally we still use Supabase Auth as the
password store + JWT issuer; the FE just sees plain HTTP endpoints.

Sign-up is invite-only â€” there is intentionally no public /auth/signup.
Admins create users via POST /admin/users/invite.

Endpoints:

- POST  /auth/login             email + password -> tokens
- POST  /auth/refresh           refresh_token   -> rotated tokens
- POST  /auth/change-password   current + new password (auth required)
- GET   /auth/me                current profile
- PATCH /auth/me/profile        update own display_name / organization
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.utils.email_validate import validate_email_str
from sqlalchemy import select, update

from app.auth.deps import current_user
from app.auth.middleware import AuthUser
from app.auth.supabase_admin import get_admin_client
from app.db.engine import get_session_factory
from app.db.models import UserProfile
from app.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)
    password: str = Field(..., min_length=1, max_length=200)

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str) -> str:
        return validate_email_str(v)


class TokenBundle(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str = "bearer"
    user: dict


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=200)
    new_password: str = Field(..., min_length=8, max_length=200)


class ProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)
    organization: str | None = Field(default=None, max_length=200)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize(user: AuthUser) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "display_name": user.display_name,
        "organization": user.organization,
    }


async def _load_profile_by_id(user_id: str) -> UserProfile | None:
    async with get_session_factory()() as session:
        row = await session.execute(
            select(UserProfile).where(UserProfile.id == user_id)
        )
        return row.scalar_one_or_none()


def _profile_to_dict(p: UserProfile) -> dict:
    return {
        "id": str(p.id),
        "email": p.email,
        "role": p.role,
        "display_name": p.display_name,
        "organization": p.organization,
    }


# ---------------------------------------------------------------------------
# Sign-in / refresh / change-password
# ---------------------------------------------------------------------------


@router.post("/login", response_model=TokenBundle, summary="Sign in with email + password")
async def login(payload: LoginRequest) -> TokenBundle:
    # Use a fresh, throwaway client so the singleton's session stays clean.
    from supabase import create_client

    from app.config import get_settings

    s = get_settings()
    client = create_client(s.supabase_url, s.supabase_anon_key or s.supabase_service_role_key)
    email = str(payload.email).lower().strip()
    try:
        result = await asyncio.to_thread(
            client.auth.sign_in_with_password,
            {"email": email, "password": payload.password},
        )
    except Exception as exc:
        msg = str(exc).lower()
        if "invalid" in msg or "credentials" in msg or "email not confirmed" in msg:
            raise HTTPException(status_code=401, detail="invalid_credentials") from exc
        raise HTTPException(status_code=400, detail=f"login_failed: {exc}") from exc

    session = getattr(result, "session", None)
    user = getattr(result, "user", None)
    if session is None or user is None:
        raise HTTPException(status_code=401, detail="invalid_credentials")

    profile = await _load_profile_by_id(user.id)
    if profile is None:
        raise HTTPException(status_code=403, detail="profile_not_found")

    return TokenBundle(
        access_token=session.access_token,
        refresh_token=session.refresh_token,
        expires_in=int(getattr(session, "expires_in", 3600) or 3600),
        user=_profile_to_dict(profile),
    )


@router.post("/refresh", response_model=TokenBundle, summary="Rotate access token using refresh token")
async def refresh(payload: RefreshRequest) -> TokenBundle:
    from supabase import create_client

    from app.config import get_settings

    s = get_settings()
    client = create_client(s.supabase_url, s.supabase_anon_key or s.supabase_service_role_key)
    try:
        result = await asyncio.to_thread(
            client.auth.refresh_session, payload.refresh_token
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail="invalid_refresh_token") from exc

    session = getattr(result, "session", None)
    user = getattr(result, "user", None)
    if session is None or user is None:
        raise HTTPException(status_code=401, detail="invalid_refresh_token")

    profile = await _load_profile_by_id(user.id)
    if profile is None:
        raise HTTPException(status_code=403, detail="profile_not_found")

    return TokenBundle(
        access_token=session.access_token,
        refresh_token=session.refresh_token,
        expires_in=int(getattr(session, "expires_in", 3600) or 3600),
        user=_profile_to_dict(profile),
    )


@router.post(
    "/change-password",
    status_code=204,
    summary="Change own password (requires current password)",
)
async def change_password(
    payload: ChangePasswordRequest,
    user: AuthUser = Depends(current_user),
) -> None:
    from supabase import create_client

    from app.config import get_settings

    s = get_settings()
    # Verify on a throwaway client so the admin singleton stays unauthenticated.
    verify_client = create_client(
        s.supabase_url, s.supabase_anon_key or s.supabase_service_role_key
    )
    try:
        await asyncio.to_thread(
            verify_client.auth.sign_in_with_password,
            {"email": user.email, "password": payload.current_password},
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail="invalid_current_password") from exc

    admin = create_client(s.supabase_url, s.supabase_service_role_key)
    try:
        await asyncio.to_thread(
            admin.auth.admin.update_user_by_id,
            user.id,
            {"password": payload.new_password},
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"password_update_failed: {exc}") from exc
    return None


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


@router.get("/me", summary="Current user profile")
async def me(user: AuthUser = Depends(current_user)) -> dict:
    return _serialize(user)


@router.patch("/me/profile", summary="Update own profile (display_name, organization)")
async def update_my_profile(
    payload: ProfileUpdate,
    request: Request,
    user: AuthUser = Depends(current_user),
) -> dict:
    new_name = payload.display_name.strip() if payload.display_name else None
    new_org = payload.organization.strip() if payload.organization else None
    values: dict = {}
    if payload.display_name is not None:
        values["display_name"] = new_name
    if payload.organization is not None:
        values["organization"] = new_org
    if not values:
        return _serialize(user)
    async with get_session_factory()() as session:
        result = await session.execute(
            update(UserProfile)
            .where(UserProfile.id == user.id)
            .values(**values)
            .returning(
                UserProfile.id,
                UserProfile.email,
                UserProfile.role,
                UserProfile.display_name,
                UserProfile.organization,
            )
        )
        row = result.one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="profile_not_found")
        await session.commit()

    refreshed = AuthUser(
        id=str(row.id),
        email=row.email,
        role=row.role,
        display_name=row.display_name,
        organization=row.organization,
    )
    request.state.user = refreshed
    return _serialize(refreshed)
