"""Admin user-management endpoints.

Two caller tiers:

- super_admin: unrestricted. Can list/invite/delete/role-change anyone,
  set per-admin user quotas, and choose the 'admin' role at invite time.
- admin: org-scoped. Can only list/invite/delete users in their own
  `organization`. Cannot promote anyone to 'admin' or 'super_admin'.
  Each invite is counted against `users_profile.user_quota` of the admin
  (0 quota = unlimited).

Invites generate a random password and email it to the new user via
Resend. The new user signs in with that password and changes it from
their profile (POST /auth/change-password).
"""

from __future__ import annotations

import asyncio
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from app.utils.email_validate import validate_email_str
from sqlalchemy import func, or_, select, update

from app.auth.deps import current_user, require_role
from app.auth.email import send_password_email
from app.auth.middleware import AuthUser
from app.auth.supabase_admin import get_admin_client
from app.db.engine import get_session_factory
from app.db.models import INVITER_ROLES, ROLE_VALUES, UserProfile
from app.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/admin/users", tags=["admin"])

# Roles an admin (non-super) is allowed to assign at invite time.
ADMIN_ASSIGNABLE_ROLES = frozenset(
    {"drilling_engineer", "geologist", "asset_manager", "qa_steward"}
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class UserRow(BaseModel):
    id: str
    email: str
    role: str
    display_name: str | None
    organization: str | None
    user_quota: int
    invited_by: str | None
    created_at: str


class UserList(BaseModel):
    items: list[UserRow]
    total: int
    limit: int
    offset: int


class RoleUpdate(BaseModel):
    role: str = Field(..., description="One of the locked role enum values")


class QuotaUpdate(BaseModel):
    user_quota: int = Field(..., ge=0, le=10_000)


class InviteRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=320)
    display_name: str | None = Field(default=None, max_length=200)

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str) -> str:
        return validate_email_str(v)
    role: str | None = Field(
        default=None,
        description="Initial role. Defaults to 'drilling_engineer'. "
        "Admins may pick any non-admin user role; super_admin may pick any.",
    )
    organization: str | None = Field(
        default=None,
        max_length=200,
        description="Org for the invitee. Ignored for admin callers (auto = caller.org).",
    )
    user_quota: int | None = Field(
        default=None,
        ge=0,
        le=10_000,
        description="Only meaningful when inviting an 'admin'. Super_admin only.",
    )


class InviteResponse(UserRow):
    message: str = "Invite email sent with a temporary password."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row(p: UserProfile) -> UserRow:
    return UserRow(
        id=str(p.id),
        email=p.email,
        role=p.role,
        display_name=p.display_name,
        organization=p.organization,
        user_quota=int(p.user_quota or 0),
        invited_by=str(p.invited_by) if p.invited_by else None,
        created_at=p.created_at.isoformat() if p.created_at else "",
    )


def _generate_password() -> str:
    return secrets.token_urlsafe(12)


def _require_inviter(caller: AuthUser) -> None:
    if caller.role not in INVITER_ROLES:
        raise HTTPException(
            status_code=403,
            detail={
                "error_code": "requires_role",
                "required_roles": sorted(INVITER_ROLES),
                "actual_role": caller.role,
            },
        )


async def _admin_invitee_count(admin_id: str) -> int:
    async with get_session_factory()() as session:
        result = await session.execute(
            select(func.count())
            .select_from(UserProfile)
            .where(UserProfile.invited_by == admin_id)
        )
        return int(result.scalar_one())


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get("", response_model=UserList, summary="List users")
async def list_users(
    q: str | None = Query(default=None, description="Substring on email or display_name"),
    role: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    caller: AuthUser = Depends(current_user),
) -> UserList:
    if caller.role not in INVITER_ROLES:
        raise HTTPException(
            status_code=403,
            detail={
                "error_code": "requires_role",
                "required_roles": sorted(INVITER_ROLES),
                "actual_role": caller.role,
            },
        )
    if role is not None and role not in ROLE_VALUES:
        raise HTTPException(status_code=400, detail="invalid_role")

    async with get_session_factory()() as session:
        stmt = select(UserProfile)
        count_stmt = select(func.count()).select_from(UserProfile)

        # Org scope for non-super admins.
        if caller.role == "admin":
            org = caller.organization
            if not org:
                raise HTTPException(
                    status_code=409, detail="admin_missing_organization"
                )
            org_cond = UserProfile.organization == org
            stmt = stmt.where(org_cond)
            count_stmt = count_stmt.where(org_cond)

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


# ---------------------------------------------------------------------------
# Role / quota updates (super_admin only)
# ---------------------------------------------------------------------------


@router.patch(
    "/{user_id}/role",
    response_model=UserRow,
    summary="Change a user's role (super_admin only)",
    dependencies=[Depends(require_role("super_admin"))],
)
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


@router.patch(
    "/{user_id}/quota",
    response_model=UserRow,
    summary="Set how many users an admin may invite (super_admin only)",
    dependencies=[Depends(require_role("super_admin"))],
)
async def update_quota(user_id: str, payload: QuotaUpdate) -> UserRow:
    async with get_session_factory()() as session:
        result = await session.execute(
            update(UserProfile)
            .where(UserProfile.id == user_id)
            .values(user_quota=payload.user_quota)
            .returning(UserProfile)
        )
        prof = result.scalar_one_or_none()
        if prof is None:
            raise HTTPException(status_code=404, detail="user_not_found")
        await session.commit()
        return _row(prof)


# ---------------------------------------------------------------------------
# Invite
# ---------------------------------------------------------------------------


@router.post(
    "/invite",
    response_model=InviteResponse,
    status_code=201,
    summary="Invite a user by email (admin or super_admin)",
)
async def invite_user(
    payload: InviteRequest,
    caller: AuthUser = Depends(current_user),
) -> InviteResponse:
    _require_inviter(caller)

    # ---- 1. Resolve role ---------------------------------------------------
    requested_role = payload.role or "drilling_engineer"
    if requested_role not in ROLE_VALUES:
        raise HTTPException(status_code=400, detail="invalid_role")

    if caller.role == "admin":
        if requested_role not in ADMIN_ASSIGNABLE_ROLES:
            raise HTTPException(
                status_code=403,
                detail={
                    "error_code": "role_not_assignable_by_admin",
                    "allowed": sorted(ADMIN_ASSIGNABLE_ROLES),
                    "requested": requested_role,
                },
            )

    # ---- 2. Resolve organization ------------------------------------------
    if caller.role == "admin":
        organization = caller.organization
        if not organization:
            raise HTTPException(
                status_code=409, detail="admin_missing_organization"
            )
    else:
        organization = (payload.organization or "").strip() or None

    # ---- 3. Quota enforcement (admins only) -------------------------------
    if caller.role == "admin":
        async with get_session_factory()() as session:
            row = (
                await session.execute(
                    select(UserProfile.user_quota).where(UserProfile.id == caller.id)
                )
            ).scalar_one_or_none()
            quota = int(row or 0)
        if quota > 0:
            current = await _admin_invitee_count(caller.id)
            if current >= quota:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error_code": "quota_exceeded",
                        "user_quota": quota,
                        "current_invitees": current,
                    },
                )

    # ---- 4. Create the auth.users row -------------------------------------
    admin_client = get_admin_client()
    password = _generate_password()
    email = str(payload.email).lower().strip()
    metadata = {"display_name": payload.display_name} if payload.display_name else {}

    try:
        created = await asyncio.to_thread(
            admin_client.auth.admin.create_user,
            {
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": metadata,
            },
        )
    except Exception as exc:
        msg = str(exc).lower()
        if "already" in msg or "exists" in msg or "registered" in msg:
            raise HTTPException(status_code=409, detail="email_already_registered") from exc
        logger.error("invite_create_failed", error=str(exc), email=email)
        raise HTTPException(status_code=400, detail=f"invite_failed: {exc}") from exc

    user = getattr(created, "user", None)
    if user is None or getattr(user, "id", None) is None:
        raise HTTPException(status_code=502, detail="invite_no_user_returned")
    user_id = user.id

    # ---- 5. Wait for handle_new_user trigger, then stamp role/org/quota --
    profile: UserProfile | None = None
    for _ in range(10):
        async with get_session_factory()() as session:
            profile = (
                await session.execute(
                    select(UserProfile).where(UserProfile.id == user_id)
                )
            ).scalar_one_or_none()
        if profile is not None:
            break
        await asyncio.sleep(0.2)
    if profile is None:
        try:
            await asyncio.to_thread(admin_client.auth.admin.delete_user, user_id)
        except Exception:
            pass
        raise HTTPException(status_code=502, detail="profile_not_created")

    quota_for_invitee = 0
    if caller.role == "super_admin" and requested_role == "admin":
        quota_for_invitee = int(payload.user_quota or 0)

    async with get_session_factory()() as session:
        result = await session.execute(
            update(UserProfile)
            .where(UserProfile.id == user_id)
            .values(
                role=requested_role,
                organization=organization,
                user_quota=quota_for_invitee,
                invited_by=caller.id,
            )
            .returning(UserProfile)
        )
        profile = result.scalar_one()
        await session.commit()

    # ---- 6. Email the password; roll back on send failure -----------------
    try:
        await send_password_email(
            to_email=email, password=password, display_name=payload.display_name
        )
    except Exception as exc:
        logger.error("invite_email_failed_rollback", error=str(exc), email=email)
        try:
            await asyncio.to_thread(admin_client.auth.admin.delete_user, user_id)
        except Exception:
            pass
        raise HTTPException(status_code=502, detail=f"email_send_failed: {exc}") from exc

    base = _row(profile)
    return InviteResponse(**base.model_dump())


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


@router.delete(
    "/{user_id}",
    status_code=204,
    summary="Delete a user (admin: own org & non-admins only; super_admin: anyone)",
)
async def delete_user(
    user_id: str,
    caller: AuthUser = Depends(current_user),
) -> None:
    _require_inviter(caller)

    async with get_session_factory()() as session:
        target = (
            await session.execute(select(UserProfile).where(UserProfile.id == user_id))
        ).scalar_one_or_none()
        if target is None:
            raise HTTPException(status_code=404, detail="user_not_found")

    if str(target.id) == caller.id:
        raise HTTPException(status_code=400, detail="cannot_delete_self")
    if caller.role == "admin":
        if target.role in ("admin", "super_admin"):
            raise HTTPException(status_code=403, detail="cannot_delete_admin_or_super")
        if target.organization != caller.organization:
            raise HTTPException(status_code=403, detail="cross_org_forbidden")

    admin_client = get_admin_client()
    try:
        await asyncio.to_thread(admin_client.auth.admin.delete_user, user_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"delete_failed: {exc}") from exc
    return None


# ---------------------------------------------------------------------------
# Resend / regenerate temporary password
# ---------------------------------------------------------------------------


@router.post(
    "/{user_id}/resend-invite",
    response_model=InviteResponse,
    summary="Generate a new temporary password and email it to the user",
)
async def resend_invite(
    user_id: str,
    caller: AuthUser = Depends(current_user),
) -> InviteResponse:
    """Re-issue an invite.

    - Generates a fresh random password and updates auth.users.
    - Emails the new password via Resend.
    - Does NOT change role/org/quota.

    Permissions:
    - super_admin: may resend for anyone except themselves.
    - admin:      may resend only for non-admin users in their own org.
    """
    _require_inviter(caller)

    async with get_session_factory()() as session:
        target = (
            await session.execute(select(UserProfile).where(UserProfile.id == user_id))
        ).scalar_one_or_none()
        if target is None:
            raise HTTPException(status_code=404, detail="user_not_found")

    if str(target.id) == caller.id:
        raise HTTPException(status_code=400, detail="cannot_resend_self")
    if caller.role == "admin":
        if target.role in ("admin", "super_admin"):
            raise HTTPException(status_code=403, detail="cannot_resend_admin_or_super")
        if target.organization != caller.organization:
            raise HTTPException(status_code=403, detail="cross_org_forbidden")

    admin_client = get_admin_client()
    new_password = _generate_password()
    try:
        await asyncio.to_thread(
            admin_client.auth.admin.update_user_by_id,
            str(target.id),
            {"password": new_password},
        )
    except Exception as exc:
        logger.error("resend_password_update_failed", error=str(exc), email=target.email)
        raise HTTPException(
            status_code=400, detail=f"password_update_failed: {exc}"
        ) from exc

    try:
        await send_password_email(
            to_email=target.email,
            password=new_password,
            display_name=target.display_name,
        )
    except Exception as exc:
        logger.error("resend_email_failed", error=str(exc), email=target.email)
        raise HTTPException(
            status_code=502, detail=f"email_send_failed: {exc}"
        ) from exc

    return InviteResponse(**_row(target).model_dump())
