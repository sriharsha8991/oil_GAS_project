"""Admin user-management endpoints. Super-admin only.

These exist because the frontend cannot (and should not) hold the
service-role key. Routes:

- GET    /admin/users                       list users
- PATCH  /admin/users/{id}/role             promote / demote
- POST   /admin/users/invite                invite by email
- DELETE /admin/users/{id}                  off-board (cascades users_profile)

All routes require role=super_admin. Anon-key sign-up remains open;
these endpoints exist so admins can manage roles, invite specific people,
and off-board without leaving the app.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, update

from app.auth.deps import require_role
from app.auth.supabase_admin import get_admin_client
from app.db.engine import get_session_factory
from app.db.models import ROLE_VALUES, UserProfile

router = APIRouter(
    prefix="/admin/users",
    tags=["admin"],
    dependencies=[Depends(require_role("super_admin"))],
)


class UserRow(BaseModel):
    id: str
    email: str
    role: str
    display_name: str | None
    organization: str | None
    created_at: str


class UserList(BaseModel):
    items: list[UserRow]
    total: int
    limit: int
    offset: int


class RoleUpdate(BaseModel):
    role: str = Field(..., description="One of the locked role enum values")


class InviteRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)
    display_name: str | None = Field(default=None, max_length=200)
    role: str | None = Field(
        default=None, description="Optional initial role; defaults to drilling_engineer"
    )


def _row(p: UserProfile) -> UserRow:
    return UserRow(
        id=str(p.id),
        email=p.email,
        role=p.role,
        display_name=p.display_name,
        organization=p.organization,
        created_at=p.created_at.isoformat() if p.created_at else "",
    )


@router.get("", response_model=UserList, summary="List users")
async def list_users(
    q: str | None = Query(default=None, description="Substring on email or display_name"),
    role: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> UserList:
    if role is not None and role not in ROLE_VALUES:
        raise HTTPException(status_code=400, detail="invalid_role")

    async with get_session_factory()() as session:
        stmt = select(UserProfile)
        count_stmt = select(func.count()).select_from(UserProfile)
        if q:
            like = f"%{q.lower()}%"
            cond = or_(
                func.lower(UserProfile.email).like(like),
                func.lower(func.coalesce(UserProfile.display_name, "")).like(like),
            )
            stmt = stmt.where(cond)
            count_stmt = count_stmt.where(cond)
        if role:
            stmt = stmt.where(UserProfile.role == role)
            count_stmt = count_stmt.where(UserProfile.role == role)

        total = (await session.execute(count_stmt)).scalar_one()
        stmt = stmt.order_by(UserProfile.created_at.desc()).limit(limit).offset(offset)
        rows = (await session.execute(stmt)).scalars().all()

    return UserList(items=[_row(p) for p in rows], total=total, limit=limit, offset=offset)


@router.patch("/{user_id}/role", response_model=UserRow, summary="Change a user's role")
async def update_role(user_id: str, payload: RoleUpdate) -> UserRow:
    if payload.role not in ROLE_VALUES:
        raise HTTPException(status_code=400, detail="invalid_role")

    async with get_session_factory()() as session:
        result = await session.execute(
            update(UserProfile)
            .where(UserProfile.id == user_id)
            .values(role=payload.role)
            .returning(UserProfile)
        )
        prof = result.scalar_one_or_none()
        if prof is None:
            raise HTTPException(status_code=404, detail="user_not_found")
        await session.commit()
        return _row(prof)


@router.post("/invite", response_model=UserRow, status_code=201, summary="Invite a user by email")
async def invite_user(payload: InviteRequest) -> UserRow:
    if payload.role is not None and payload.role not in ROLE_VALUES:
        raise HTTPException(status_code=400, detail="invalid_role")

    admin = get_admin_client()
    metadata: dict = {}
    if payload.display_name:
        metadata["display_name"] = payload.display_name

    try:
        result = admin.auth.admin.invite_user_by_email(
            payload.email, options={"data": metadata} if metadata else None
        )
    except Exception as exc:  # supabase-py raises a generic AuthApiError
        raise HTTPException(status_code=400, detail=f"invite_failed: {exc}") from exc

    user = getattr(result, "user", None)
    if user is None or getattr(user, "id", None) is None:
        raise HTTPException(status_code=502, detail="invite_no_user_returned")

    user_id = user.id

    # The handle_new_user trigger has populated users_profile by now.
    # If a non-default role was requested, apply it.
    async with get_session_factory()() as session:
        if payload.role:
            await session.execute(
                update(UserProfile)
                .where(UserProfile.id == user_id)
                .values(role=payload.role)
            )
            await session.commit()
        prof = (
            await session.execute(select(UserProfile).where(UserProfile.id == user_id))
        ).scalar_one_or_none()
        if prof is None:
            raise HTTPException(status_code=502, detail="profile_not_created")
        return _row(prof)


@router.delete("/{user_id}", status_code=204, summary="Delete a user (cascades profile)")
async def delete_user(user_id: str) -> None:
    admin = get_admin_client()
    try:
        admin.auth.admin.delete_user(user_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"delete_failed: {exc}") from exc
    return None
