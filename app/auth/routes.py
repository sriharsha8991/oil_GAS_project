"""Auth-related endpoints.

Per ROLES + Supabase pivot we do NOT implement /auth/signup, /auth/login,
or password reset on the backend — the frontend uses @supabase/supabase-js
directly. Endpoints here cover only what the frontend cannot do via the
Supabase JS SDK:

- GET   /auth/me                current profile
- PATCH /auth/me/profile        update own display_name
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import update

from app.auth.deps import current_user
from app.auth.middleware import AuthUser
from app.db.engine import get_session_factory
from app.db.models import UserProfile

router = APIRouter(prefix="/auth", tags=["auth"])


class ProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)
    organization: str | None = Field(default=None, max_length=200)


def _serialize(user: AuthUser) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "display_name": user.display_name,
        "organization": user.organization,
    }


@router.get("/me", summary="Current user profile")
async def me(user: AuthUser = Depends(current_user)) -> dict:
    return _serialize(user)


@router.patch("/me/profile", summary="Update own profile (display_name)")
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
