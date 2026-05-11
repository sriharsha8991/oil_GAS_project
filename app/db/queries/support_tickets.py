"""Persistent reads/writes for support tickets, attachments, notes."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    SupportTicket,
    SupportTicketAttachment,
    SupportTicketNote,
    UserProfile,
)


async def create_ticket(
    sess: AsyncSession,
    *,
    user_id: str,
    category: str,
    subject: str,
    message: str,
    diagnostics: dict[str, Any] | None,
) -> SupportTicket:
    """Insert a new support ticket. Caller commits."""
    ticket = SupportTicket(
        user_id=uuid.UUID(user_id),
        category=category,
        subject=subject,
        message=message,
        diagnostics=diagnostics,
    )
    sess.add(ticket)
    await sess.flush()
    return ticket


async def add_attachment(
    sess: AsyncSession,
    *,
    ticket_id: str,
    storage_key: str,
    filename: str,
    mime_type: str,
    size_bytes: int,
) -> SupportTicketAttachment:
    att = SupportTicketAttachment(
        ticket_id=uuid.UUID(ticket_id),
        storage_key=storage_key,
        filename=filename,
        mime_type=mime_type,
        size_bytes=size_bytes,
    )
    sess.add(att)
    await sess.flush()
    return att


async def mark_email_sent(
    sess: AsyncSession,
    *,
    ticket_id: str,
    message_id: str | None,
) -> None:
    ticket = await sess.get(SupportTicket, uuid.UUID(ticket_id))
    if ticket is None:
        return
    ticket.email_sent = True
    ticket.email_message_id = message_id


async def list_my_tickets(
    sess: AsyncSession,
    *,
    user_id: str,
    limit: int,
    cursor: str | None,
    status: str | None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Cursor-paginated list of the user's tickets (newest first)."""
    stmt = (
        select(SupportTicket)
        .where(
            SupportTicket.user_id == uuid.UUID(user_id),
            SupportTicket.deleted_at.is_(None),
        )
        .order_by(desc(SupportTicket.created_at), desc(SupportTicket.id))
        .limit(limit + 1)
    )
    if status:
        stmt = stmt.where(SupportTicket.status == status)
    if cursor:
        try:
            cursor_uuid = uuid.UUID(cursor)
        except ValueError:
            cursor_uuid = None
        if cursor_uuid is not None:
            anchor = await sess.get(SupportTicket, cursor_uuid)
            if anchor is not None:
                stmt = stmt.where(
                    (SupportTicket.created_at < anchor.created_at)
                    | (
                        (SupportTicket.created_at == anchor.created_at)
                        & (SupportTicket.id < anchor.id)
                    )
                )
    rows = list((await sess.execute(stmt)).scalars().all())

    next_cursor: str | None = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = str(rows[-1].id)

    if not rows:
        return [], next_cursor

    # Attachment counts + last admin note in single bounded queries.
    ticket_ids = [r.id for r in rows]
    counts: dict[uuid.UUID, int] = {tid: 0 for tid in ticket_ids}
    att_rows = (await sess.execute(
        select(SupportTicketAttachment.ticket_id)
        .where(SupportTicketAttachment.ticket_id.in_(ticket_ids))
    )).scalars().all()
    for tid in att_rows:
        counts[tid] = counts.get(tid, 0) + 1

    last_note: dict[uuid.UUID, str] = {}
    note_rows = (await sess.execute(
        select(SupportTicketNote)
        .where(SupportTicketNote.ticket_id.in_(ticket_ids))
        .order_by(SupportTicketNote.ticket_id, desc(SupportTicketNote.created_at))
    )).scalars().all()
    for n in note_rows:
        last_note.setdefault(n.ticket_id, n.text)

    items = [
        {
            "ticket_id": str(r.id),
            "category": r.category,
            "subject": r.subject,
            "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            "last_admin_note": last_note.get(r.id),
            "attachment_count": counts.get(r.id, 0),
        }
        for r in rows
    ]
    return items, next_cursor


async def get_my_ticket(
    sess: AsyncSession,
    *,
    user_id: str,
    ticket_id: str,
) -> dict[str, Any] | None:
    """Return full detail for a ticket the user owns (or None)."""
    try:
        tid = uuid.UUID(ticket_id)
    except ValueError:
        return None
    ticket = (await sess.execute(
        select(SupportTicket).where(
            SupportTicket.id == tid,
            SupportTicket.user_id == uuid.UUID(user_id),
            SupportTicket.deleted_at.is_(None),
        )
    )).scalar_one_or_none()
    if ticket is None:
        return None

    atts = (await sess.execute(
        select(SupportTicketAttachment)
        .where(SupportTicketAttachment.ticket_id == tid)
        .order_by(SupportTicketAttachment.created_at)
    )).scalars().all()
    notes = (await sess.execute(
        select(SupportTicketNote)
        .where(SupportTicketNote.ticket_id == tid)
        .order_by(SupportTicketNote.created_at)
    )).scalars().all()

    return {
        "ticket_id": str(ticket.id),
        "category": ticket.category,
        "subject": ticket.subject,
        "message": ticket.message,
        "status": ticket.status,
        "diagnostics": ticket.diagnostics,
        "attachments": [
            {
                "id": str(a.id),
                "filename": a.filename,
                "mime_type": a.mime_type,
                "size_bytes": a.size_bytes,
                "url": (
                    f"/support/tickets/{ticket.id}/attachments/{a.id}"
                ),
            }
            for a in atts
        ],
        "notes": [
            {
                "id": str(n.id),
                "author": "admin",
                "text": n.text,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in notes
        ],
        "created_at": ticket.created_at.isoformat() if ticket.created_at else None,
        "updated_at": ticket.updated_at.isoformat() if ticket.updated_at else None,
    }


async def get_attachment_for_user(
    sess: AsyncSession,
    *,
    user_id: str,
    ticket_id: str,
    attachment_id: str,
    is_super_admin: bool,
) -> SupportTicketAttachment | None:
    """Fetch one attachment row, scoped to owner unless super-admin."""
    try:
        tid = uuid.UUID(ticket_id)
        aid = uuid.UUID(attachment_id)
    except ValueError:
        return None
    ticket = (await sess.execute(
        select(SupportTicket).where(
            SupportTicket.id == tid,
            SupportTicket.deleted_at.is_(None),
        )
    )).scalar_one_or_none()
    if ticket is None:
        return None
    if not is_super_admin and str(ticket.user_id) != user_id:
        return None
    return (await sess.execute(
        select(SupportTicketAttachment).where(
            SupportTicketAttachment.id == aid,
            SupportTicketAttachment.ticket_id == tid,
        )
    )).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Admin (Slice 2): triage, status updates, notes, soft-delete
# ---------------------------------------------------------------------------


def _serialize_user_block(profile: UserProfile | None, fallback_id: uuid.UUID) -> dict[str, Any]:
    if profile is None:
        return {"id": str(fallback_id), "email": None, "name": None}
    return {
        "id": str(profile.id),
        "email": profile.email,
        "name": profile.display_name,
    }


async def list_admin_tickets(
    sess: AsyncSession,
    *,
    limit: int,
    cursor: str | None,
    status: str | None,
    category: str | None,
    user_id: str | None,
    q: str | None,
) -> tuple[list[dict[str, Any]], str | None, dict[str, int]]:
    """Cross-user triage list (newest first). Soft-deleted excluded."""
    stmt = (
        select(SupportTicket)
        .where(SupportTicket.deleted_at.is_(None))
        .order_by(desc(SupportTicket.created_at), desc(SupportTicket.id))
        .limit(limit + 1)
    )
    if status:
        stmt = stmt.where(SupportTicket.status == status)
    if category:
        stmt = stmt.where(SupportTicket.category == category)
    if user_id:
        try:
            stmt = stmt.where(SupportTicket.user_id == uuid.UUID(user_id))
        except ValueError:
            return [], None, {}
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(SupportTicket.subject.ilike(like), SupportTicket.message.ilike(like))
        )
    if cursor:
        try:
            cursor_uuid = uuid.UUID(cursor)
        except ValueError:
            cursor_uuid = None
        if cursor_uuid is not None:
            anchor = await sess.get(SupportTicket, cursor_uuid)
            if anchor is not None:
                stmt = stmt.where(
                    (SupportTicket.created_at < anchor.created_at)
                    | (
                        (SupportTicket.created_at == anchor.created_at)
                        & (SupportTicket.id < anchor.id)
                    )
                )

    rows = list((await sess.execute(stmt)).scalars().all())
    next_cursor: str | None = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = str(rows[-1].id)

    # Counts (always against the full table, not the page).
    counts_rows = (await sess.execute(
        select(SupportTicket.status, func.count(SupportTicket.id))
        .where(SupportTicket.deleted_at.is_(None))
        .group_by(SupportTicket.status)
    )).all()
    counts = {s: 0 for s in ("open", "in_progress", "resolved", "closed")}
    for status_val, n in counts_rows:
        counts[status_val] = int(n)

    if not rows:
        return [], next_cursor, counts

    # User profiles + attachment counts in single bounded queries.
    user_ids = {r.user_id for r in rows}
    profile_rows = (await sess.execute(
        select(UserProfile).where(UserProfile.id.in_(user_ids))
    )).scalars().all()
    profiles = {p.id: p for p in profile_rows}

    ticket_ids = [r.id for r in rows]
    att_counts: dict[uuid.UUID, int] = {tid: 0 for tid in ticket_ids}
    for tid in (await sess.execute(
        select(SupportTicketAttachment.ticket_id)
        .where(SupportTicketAttachment.ticket_id.in_(ticket_ids))
    )).scalars().all():
        att_counts[tid] = att_counts.get(tid, 0) + 1

    items = [
        {
            "ticket_id": str(r.id),
            "category": r.category,
            "subject": r.subject,
            "status": r.status,
            "user": _serialize_user_block(profiles.get(r.user_id), r.user_id),
            "attachment_count": att_counts.get(r.id, 0),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in rows
    ]
    return items, next_cursor, counts


async def get_admin_ticket(
    sess: AsyncSession,
    *,
    ticket_id: str,
) -> dict[str, Any] | None:
    """Full ticket detail for admin (any owner). None if missing/deleted."""
    try:
        tid = uuid.UUID(ticket_id)
    except ValueError:
        return None
    ticket = (await sess.execute(
        select(SupportTicket).where(
            SupportTicket.id == tid,
            SupportTicket.deleted_at.is_(None),
        )
    )).scalar_one_or_none()
    if ticket is None:
        return None

    profile = (await sess.execute(
        select(UserProfile).where(UserProfile.id == ticket.user_id)
    )).scalar_one_or_none()
    atts = (await sess.execute(
        select(SupportTicketAttachment)
        .where(SupportTicketAttachment.ticket_id == tid)
        .order_by(SupportTicketAttachment.created_at)
    )).scalars().all()
    notes = (await sess.execute(
        select(SupportTicketNote)
        .where(SupportTicketNote.ticket_id == tid)
        .order_by(SupportTicketNote.created_at)
    )).scalars().all()

    return {
        "ticket_id": str(ticket.id),
        "category": ticket.category,
        "subject": ticket.subject,
        "message": ticket.message,
        "status": ticket.status,
        "diagnostics": ticket.diagnostics,
        "email_sent": ticket.email_sent,
        "user": _serialize_user_block(profile, ticket.user_id),
        "attachments": [
            {
                "id": str(a.id),
                "filename": a.filename,
                "mime_type": a.mime_type,
                "size_bytes": a.size_bytes,
                "url": f"/support/tickets/{ticket.id}/attachments/{a.id}",
            }
            for a in atts
        ],
        "notes": [
            {
                "id": str(n.id),
                "author_user_id": str(n.author_user_id),
                "text": n.text,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in notes
        ],
        "created_at": ticket.created_at.isoformat() if ticket.created_at else None,
        "updated_at": ticket.updated_at.isoformat() if ticket.updated_at else None,
    }


async def get_ticket_with_owner(
    sess: AsyncSession,
    *,
    ticket_id: str,
) -> tuple[SupportTicket, UserProfile | None] | None:
    """Returns the ticket row + owner profile (for emails). None if missing."""
    try:
        tid = uuid.UUID(ticket_id)
    except ValueError:
        return None
    ticket = (await sess.execute(
        select(SupportTicket).where(
            SupportTicket.id == tid,
            SupportTicket.deleted_at.is_(None),
        )
    )).scalar_one_or_none()
    if ticket is None:
        return None
    profile = (await sess.execute(
        select(UserProfile).where(UserProfile.id == ticket.user_id)
    )).scalar_one_or_none()
    return ticket, profile


async def update_ticket_status(
    sess: AsyncSession,
    *,
    ticket_id: str,
    status: str,
) -> SupportTicket | None:
    ticket = await sess.get(SupportTicket, uuid.UUID(ticket_id))
    if ticket is None or ticket.deleted_at is not None:
        return None
    ticket.status = status
    return ticket


async def add_admin_note(
    sess: AsyncSession,
    *,
    ticket_id: str,
    author_user_id: str,
    text: str,
) -> SupportTicketNote:
    note = SupportTicketNote(
        ticket_id=uuid.UUID(ticket_id),
        author_user_id=uuid.UUID(author_user_id),
        text=text,
    )
    sess.add(note)
    await sess.flush()
    return note


async def soft_delete_ticket(
    sess: AsyncSession,
    *,
    ticket_id: str,
) -> bool:
    ticket = await sess.get(SupportTicket, uuid.UUID(ticket_id))
    if ticket is None or ticket.deleted_at is not None:
        return False
    ticket.deleted_at = datetime.now(timezone.utc)
    return True
