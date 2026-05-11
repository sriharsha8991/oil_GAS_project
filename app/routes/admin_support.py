"""Admin (super_admin) triage surface for support tickets — Slice 2.

GET    /admin/support/tickets               filtered/paginated triage list
GET    /admin/support/tickets/{id}          full detail (with owner block)
PATCH  /admin/support/tickets/{id}          status and/or note (+ user notify)
DELETE /admin/support/tickets/{id}          soft delete (sets deleted_at)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth.deps import require_role
from app.auth.email import send_support_ticket_update_email
from app.auth.middleware import AuthUser
from app.config import get_settings
from app.db.engine import get_session_factory
from app.db.models import (
    SUPPORT_TICKET_CATEGORIES,
    SUPPORT_TICKET_STATUSES,
)
from app.db.queries.support_tickets import (
    add_admin_note,
    get_admin_ticket,
    get_ticket_with_owner,
    list_admin_tickets,
    soft_delete_ticket,
    update_ticket_status,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(
    prefix="/admin/support",
    tags=["admin-support"],
    dependencies=[Depends(require_role("super_admin"))],
)


class AdminListResponse(BaseModel):
    items: list[dict[str, Any]]
    next_cursor: str | None = None
    counts: dict[str, int]


class PatchTicketRequest(BaseModel):
    status: str | None = Field(default=None)
    note: str | None = Field(default=None, min_length=1, max_length=4000)


class PatchTicketResponse(BaseModel):
    ticket_id: str
    status: str
    note_added: bool
    user_notified: bool


# ---------------------------------------------------------------------------
# GET /admin/support/tickets
# ---------------------------------------------------------------------------


@router.get("/tickets", response_model=AdminListResponse, summary="List all tickets (triage)")
async def list_tickets(
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(None),
    status: str | None = Query(None),
    category: str | None = Query(None),
    user_id: str | None = Query(None),
    q: str | None = Query(None, max_length=200),
) -> AdminListResponse:
    if status is not None and status not in SUPPORT_TICKET_STATUSES:
        raise HTTPException(
            status_code=400,
            detail={"error_code": "invalid_status", "allowed": list(SUPPORT_TICKET_STATUSES)},
        )
    if category is not None and category not in SUPPORT_TICKET_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail={"error_code": "invalid_category", "allowed": list(SUPPORT_TICKET_CATEGORIES)},
        )
    Session = get_session_factory()
    async with Session() as sess:
        items, next_cursor, counts = await list_admin_tickets(
            sess,
            limit=limit, cursor=cursor, status=status,
            category=category, user_id=user_id, q=q,
        )
    return AdminListResponse(items=items, next_cursor=next_cursor, counts=counts)


# ---------------------------------------------------------------------------
# GET /admin/support/tickets/{id}
# ---------------------------------------------------------------------------


@router.get("/tickets/{ticket_id}", summary="Single ticket detail (admin view)")
async def get_ticket(ticket_id: str) -> dict[str, Any]:
    Session = get_session_factory()
    async with Session() as sess:
        detail = await get_admin_ticket(sess, ticket_id=ticket_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="ticket_not_found")
    return detail


# ---------------------------------------------------------------------------
# PATCH /admin/support/tickets/{id}
# ---------------------------------------------------------------------------


@router.patch(
    "/tickets/{ticket_id}",
    response_model=PatchTicketResponse,
    summary="Update status and/or add a note",
)
async def patch_ticket(
    ticket_id: str,
    body: PatchTicketRequest,
    admin: AuthUser = Depends(require_role("super_admin")),
) -> PatchTicketResponse:
    new_status = body.status
    note_text = body.note.strip() if body.note else None

    if new_status is None and not note_text:
        raise HTTPException(
            status_code=400, detail="status_or_note_required",
        )
    if new_status is not None and new_status not in SUPPORT_TICKET_STATUSES:
        raise HTTPException(
            status_code=400,
            detail={"error_code": "invalid_status", "allowed": list(SUPPORT_TICKET_STATUSES)},
        )

    settings = get_settings()
    Session = get_session_factory()
    async with Session() as sess:
        loaded = await get_ticket_with_owner(sess, ticket_id=ticket_id)
        if loaded is None:
            raise HTTPException(status_code=404, detail="ticket_not_found")
        ticket, owner = loaded

        if new_status is not None:
            await update_ticket_status(sess, ticket_id=ticket_id, status=new_status)
        if note_text:
            await add_admin_note(
                sess, ticket_id=ticket_id, author_user_id=admin.id, text=note_text,
            )
        await sess.commit()
        # Re-read final status (may have just been updated).
        final_status = new_status or ticket.status
        subject = ticket.subject

    user_notified = False
    if settings.support_notify_user_on_update and owner is not None and owner.email:
        try:
            await send_support_ticket_update_email(
                ticket_id=ticket_id,
                subject=subject,
                user_email=owner.email,
                user_display_name=owner.display_name,
                new_status=new_status,
                note_text=note_text,
            )
            user_notified = True
        except Exception as exc:  # noqa: BLE001
            # Email failure does NOT roll back the patch — log and surface
            # via response so admin UI can show "saved, email failed".
            logger.warning(
                "support_update_email_failed",
                ticket_id=ticket_id, error=str(exc),
            )

    logger.info(
        "support_ticket_patched",
        ticket_id=ticket_id, admin_id=admin.id,
        status_changed=bool(new_status), note_added=bool(note_text),
        user_notified=user_notified,
    )
    return PatchTicketResponse(
        ticket_id=ticket_id,
        status=final_status,
        note_added=bool(note_text),
        user_notified=user_notified,
    )


# ---------------------------------------------------------------------------
# DELETE /admin/support/tickets/{id}
# ---------------------------------------------------------------------------


@router.delete("/tickets/{ticket_id}", summary="Soft-delete a ticket")
async def delete_ticket(ticket_id: str) -> dict[str, Any]:
    Session = get_session_factory()
    async with Session() as sess:
        ok = await soft_delete_ticket(sess, ticket_id=ticket_id)
        if not ok:
            raise HTTPException(status_code=404, detail="ticket_not_found")
        await sess.commit()
    logger.info("support_ticket_soft_deleted", ticket_id=ticket_id)
    return {"ticket_id": ticket_id, "deleted": True}
