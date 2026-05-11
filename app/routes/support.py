"""In-app support tickets — user-facing surface (Slice 1).

POST   /support/tickets                          submit a ticket (multipart)
GET    /support/tickets/me                       cursor-paginated history
GET    /support/tickets/me/{ticket_id}           full detail (owner only)
GET    /support/tickets/{id}/attachments/{aid}   302 -> short-TTL signed URL

Slice 2 (deferred): /admin/support/* triage endpoints, status-change emails,
rate limiting, soft-delete.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.auth.deps import current_user
from app.auth.email import send_support_ticket_email
from app.auth.middleware import AuthUser
from app.config import get_settings
from app.db.engine import get_session_factory
from app.db.models import SUPPORT_TICKET_CATEGORIES, SUPPORT_TICKET_STATUSES
from app.db.queries.support_tickets import (
    add_attachment,
    create_ticket,
    get_attachment_for_user,
    get_my_ticket,
    list_my_tickets,
    mark_email_sent,
)
from app.storage.supabase_storage import SupabaseStorage
from app.utils.logging import get_logger
from app.utils.rate_limit import enforce_support_submission

logger = get_logger(__name__)

router = APIRouter(prefix="/support", tags=["support"])


_ALLOWED_MIME_TYPES = frozenset({
    "image/png",
    "image/jpeg",
    "application/pdf",
    "text/plain",
})

_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(name: str) -> str:
    """Strip path components and collapse unsafe characters."""
    base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] or "attachment"
    cleaned = _FILENAME_SAFE.sub("_", base).strip("._") or "attachment"
    return cleaned[:120]


# ---------------------------------------------------------------------------
# Response shapes — kept loose (dict) to match the FE spec verbatim.
# ---------------------------------------------------------------------------


class CreateTicketResponse(BaseModel):
    ticket_id: str
    status: str
    email_sent: bool
    created_at: str


class ListItem(BaseModel):
    ticket_id: str
    category: str
    subject: str
    status: str
    created_at: str | None
    updated_at: str | None
    last_admin_note: str | None = None
    attachment_count: int


class ListResponse(BaseModel):
    items: list[ListItem]
    next_cursor: str | None = None


# ---------------------------------------------------------------------------
# POST /support/tickets — submit
# ---------------------------------------------------------------------------


@router.post(
    "/tickets",
    response_model=CreateTicketResponse,
    summary="Submit a support ticket",
)
async def submit_ticket(
    category: str = Form(...),
    subject: str = Form(..., min_length=1, max_length=120),
    message: str = Form(..., min_length=1, max_length=4000),
    include_diagnostics: bool = Form(False),
    diagnostics: str | None = Form(None),
    attachments: list[UploadFile] | None = File(None),
    user: AuthUser = Depends(current_user),
) -> CreateTicketResponse:
    settings = get_settings()

    # ---- rate limit (per-user hour + day buckets, fail-open if Redis down)
    decision = await enforce_support_submission(
        user_id=user.id,
        per_hour=settings.support_rate_limit_per_hour,
        per_day=settings.support_rate_limit_per_day,
    )
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error_code": "rate_limited",
                "retry_after_seconds": decision.retry_after_seconds,
            },
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )

    # ---- validate scalar fields ----
    if category not in SUPPORT_TICKET_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "invalid_category",
                "allowed": list(SUPPORT_TICKET_CATEGORIES),
            },
        )

    diagnostics_obj: dict[str, Any] | None = None
    if include_diagnostics:
        if not diagnostics:
            raise HTTPException(
                status_code=400, detail="diagnostics_required_when_included"
            )
        try:
            parsed = json.loads(diagnostics)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=400, detail=f"diagnostics_not_json: {exc}"
            ) from exc
        if not isinstance(parsed, dict):
            raise HTTPException(
                status_code=400, detail="diagnostics_must_be_object"
            )
        diagnostics_obj = parsed

    # ---- validate attachments (count + size + mime) ----
    files = [f for f in (attachments or []) if f and f.filename]
    if len(files) > settings.support_max_attachments:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "too_many_attachments",
                "max": settings.support_max_attachments,
            },
        )

    file_payloads: list[dict[str, Any]] = []
    for upload in files:
        mime = (upload.content_type or "").lower()
        if mime not in _ALLOWED_MIME_TYPES:
            raise HTTPException(
                status_code=400,
                detail={
                    "error_code": "unsupported_attachment_type",
                    "filename": upload.filename,
                    "mime_type": mime,
                    "allowed": sorted(_ALLOWED_MIME_TYPES),
                },
            )
        data = await upload.read()
        if len(data) > settings.support_max_attachment_bytes:
            raise HTTPException(
                status_code=413,
                detail={
                    "error_code": "attachment_too_large",
                    "filename": upload.filename,
                    "max_bytes": settings.support_max_attachment_bytes,
                },
            )
        file_payloads.append({
            "filename": _safe_filename(upload.filename or "attachment"),
            "mime_type": mime,
            "data": data,
        })

    # ---- persist ticket + attachments (single transaction) ----
    storage = SupabaseStorage(settings)
    Session = get_session_factory()
    async with Session() as sess:
        ticket = await create_ticket(
            sess,
            user_id=user.id,
            category=category,
            subject=subject.strip(),
            message=message.strip(),
            diagnostics=diagnostics_obj,
        )
        ticket_id = str(ticket.id)
        created_at = ticket.created_at

        attachment_meta: list[dict[str, Any]] = []
        for f in file_payloads:
            # Filename collisions inside the same ticket are avoided by
            # prefixing a short attachment uuid; users see the original
            # filename via the row metadata.
            object_filename = f"{uuid.uuid4().hex[:8]}_{f['filename']}"
            storage_key = await storage.write_support_attachment(
                user_id=user.id,
                ticket_id=ticket_id,
                filename=object_filename,
                data=f["data"],
                content_type=f["mime_type"],
            )
            await add_attachment(
                sess,
                ticket_id=ticket_id,
                storage_key=storage_key,
                filename=f["filename"],
                mime_type=f["mime_type"],
                size_bytes=len(f["data"]),
            )
            attachment_meta.append({
                "filename": f["filename"],
                "size_bytes": len(f["data"]),
                "storage_key": storage_key,
            })
        await sess.commit()

    # ---- send email (best-effort, but failure -> 502) ----
    signed_attachments: list[dict[str, Any]] = []
    for a in attachment_meta:
        try:
            url = storage.signed_url_support_attachment(
                a["storage_key"],
                ttl_seconds=settings.support_attachment_ttl_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "support_attachment_sign_failed",
                ticket_id=ticket_id, error=str(exc),
            )
            url = ""
        signed_attachments.append({
            "filename": a["filename"],
            "size_bytes": a["size_bytes"],
            "url": url,
        })

    email_sent = False
    try:
        message_id = await send_support_ticket_email(
            ticket_id=ticket_id,
            category=category,
            subject=subject.strip(),
            message=message.strip(),
            user_email=user.email,
            user_display_name=user.display_name,
            diagnostics=diagnostics_obj,
            attachment_signed_urls=signed_attachments,
        )
        email_sent = True
        async with Session() as sess:
            await mark_email_sent(
                sess, ticket_id=ticket_id, message_id=message_id,
            )
            await sess.commit()
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "support_email_send_failed",
            ticket_id=ticket_id, error=str(exc),
        )
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "email_upstream_failed",
                "ticket_id": ticket_id,
                "message": "ticket saved but email could not be sent",
            },
        ) from exc

    logger.info(
        "support_ticket_created",
        ticket_id=ticket_id,
        user_id=user.id,
        category=category,
        attachment_count=len(file_payloads),
        diagnostics=bool(diagnostics_obj),
    )
    return CreateTicketResponse(
        ticket_id=ticket_id,
        status="open",
        email_sent=email_sent,
        created_at=created_at.isoformat() if created_at else "",
    )


# ---------------------------------------------------------------------------
# GET /support/tickets/me — history
# ---------------------------------------------------------------------------


@router.get(
    "/tickets/me",
    response_model=ListResponse,
    summary="My support tickets (newest first, cursor-paginated)",
)
async def list_my_tickets_route(
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(None),
    status: str | None = Query(None),
    user: AuthUser = Depends(current_user),
) -> ListResponse:
    if status is not None and status not in SUPPORT_TICKET_STATUSES:
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "invalid_status",
                "allowed": list(SUPPORT_TICKET_STATUSES),
            },
        )
    Session = get_session_factory()
    async with Session() as sess:
        items, next_cursor = await list_my_tickets(
            sess,
            user_id=user.id,
            limit=limit,
            cursor=cursor,
            status=status,
        )
    return ListResponse(
        items=[ListItem(**item) for item in items],
        next_cursor=next_cursor,
    )


# ---------------------------------------------------------------------------
# GET /support/tickets/me/{ticket_id} — detail
# ---------------------------------------------------------------------------


@router.get(
    "/tickets/me/{ticket_id}",
    summary="Single ticket detail (owner only)",
)
async def get_my_ticket_route(
    ticket_id: str,
    user: AuthUser = Depends(current_user),
) -> dict[str, Any]:
    Session = get_session_factory()
    async with Session() as sess:
        detail = await get_my_ticket(
            sess, user_id=user.id, ticket_id=ticket_id,
        )
    if detail is None:
        raise HTTPException(status_code=404, detail="ticket_not_found")
    return detail


# ---------------------------------------------------------------------------
# GET /support/tickets/{id}/attachments/{aid} — signed-URL redirect
# ---------------------------------------------------------------------------


@router.get(
    "/tickets/{ticket_id}/attachments/{attachment_id}",
    summary="Fetch a ticket attachment (redirects to a signed URL)",
)
async def fetch_attachment(
    ticket_id: str,
    attachment_id: str,
    user: AuthUser = Depends(current_user),
) -> RedirectResponse:
    settings = get_settings()
    Session = get_session_factory()
    async with Session() as sess:
        att = await get_attachment_for_user(
            sess,
            user_id=user.id,
            ticket_id=ticket_id,
            attachment_id=attachment_id,
            is_super_admin=(user.role == "super_admin"),
        )
    if att is None:
        raise HTTPException(status_code=404, detail="attachment_not_found")

    storage = SupabaseStorage(settings)
    try:
        url = storage.signed_url_support_attachment(att.storage_key)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "support_attachment_sign_failed",
            ticket_id=ticket_id,
            attachment_id=attachment_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=502, detail="attachment_sign_failed"
        ) from exc
    return RedirectResponse(url=url, status_code=302)
