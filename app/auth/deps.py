"""FastAPI dependencies for auth/role enforcement."""

from __future__ import annotations

from fastapi import HTTPException, Request

from app.auth.middleware import AuthUser


def current_user(request: Request) -> AuthUser:
    user = getattr(request.state, "user", None)
    if user is None:
        # Should be unreachable: middleware would have returned 401.
        raise HTTPException(status_code=401, detail="unauthorized")
    return user


def require_role(*roles: str):
    """Dependency factory: enforce that the caller has one of the given roles."""

    allowed = frozenset(roles)

    def _dep(request: Request) -> AuthUser:
        user = current_user(request)
        if user.role not in allowed:
            raise HTTPException(
                status_code=403,
                detail={
                    "error_code": "requires_role",
                    "required_roles": sorted(allowed),
                    "actual_role": user.role,
                },
            )
        return user

    return _dep
