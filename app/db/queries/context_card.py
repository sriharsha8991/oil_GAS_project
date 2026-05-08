"""Context-card builder for the chat triage layer.

Pulls a small, bounded snapshot of what the user owns (projects, recent
documents, in-progress jobs) plus their profile + permission summary so
the triage LLM can answer meta/account/catalog questions without ever
touching Qdrant or the document RAG pipeline.

Designed to be **cheap** (a single async session, a handful of small
queries against indexed columns) and **bounded** (lists are capped) so
the resulting JSON stays well under 1k tokens.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_session_factory
from app.db.models import Document, Job, Project, UserProfile
from app.db.queries.memories import fetch_top_memories, get_memory_enabled

MAX_PROJECTS = 10
MAX_DOCUMENTS = 10
MAX_IN_PROGRESS = 5


async def _project_doc_counts(
    sess: AsyncSession, user_id: str
) -> list[dict[str, Any]]:
    rows = (
        await sess.execute(
            select(
                Project.id,
                Project.name,
                func.count(Document.id).label("doc_count"),
            )
            .outerjoin(Document, Document.project_id == Project.id)
            .where(Project.user_id == user_id)
            .group_by(Project.id, Project.name, Project.created_at)
            .order_by(Project.created_at.desc())
            .limit(MAX_PROJECTS)
        )
    ).all()
    return [
        {"id": str(r.id), "name": r.name, "doc_count": int(r.doc_count or 0)}
        for r in rows
    ]


async def _recent_documents(
    sess: AsyncSession, user_id: str
) -> list[dict[str, Any]]:
    # Latest job per document via correlated subquery (small N, no need for window fn).
    rows = (
        await sess.execute(
            select(
                Document.id,
                Document.filename,
                Project.name.label("project_name"),
                Document.created_at,
            )
            .join(Project, Project.id == Document.project_id)
            .where(Document.user_id == user_id)
            .order_by(Document.created_at.desc())
            .limit(MAX_DOCUMENTS)
        )
    ).all()
    if not rows:
        return []
    doc_ids = [r.id for r in rows]
    job_rows = (
        await sess.execute(
            select(
                Job.document_id,
                Job.status,
                Job.pages_total,
            )
            .where(Job.document_id.in_(doc_ids))
            .order_by(Job.document_id, Job.created_at.desc())
        )
    ).all()
    latest_by_doc: dict[Any, tuple[str, int | None]] = {}
    for j in job_rows:
        if j.document_id not in latest_by_doc:
            latest_by_doc[j.document_id] = (j.status, j.pages_total)
    out: list[dict[str, Any]] = []
    for r in rows:
        status, pages = latest_by_doc.get(r.id, (None, None))
        out.append(
            {
                "id": str(r.id),
                "filename": r.filename,
                "project_name": r.project_name,
                "status": status,
                "pages_total": pages,
            }
        )
    return out


async def _in_progress(
    sess: AsyncSession, user_id: str
) -> list[dict[str, Any]]:
    rows = (
        await sess.execute(
            select(
                Document.filename,
                Job.current_phase,
                Job.pages_done,
                Job.pages_total,
            )
            .join(Document, Document.id == Job.document_id)
            .where(Job.user_id == user_id, Job.status == "running")
            .order_by(Job.created_at.desc())
            .limit(MAX_IN_PROGRESS)
        )
    ).all()
    return [
        {
            "filename": r.filename,
            "current_phase": r.current_phase,
            "pages_done": r.pages_done,
            "pages_total": r.pages_total,
        }
        for r in rows
    ]


async def _resolve_scope_names(
    sess: AsyncSession,
    user_id: str,
    project_id: str | None,
    document_id: str | None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "current_project_id": project_id,
        "current_project_name": None,
        "current_document_id": document_id,
        "current_document_name": None,
    }
    if project_id:
        row = (
            await sess.execute(
                select(Project.name).where(
                    Project.id == project_id, Project.user_id == user_id
                )
            )
        ).first()
        if row is not None:
            out["current_project_name"] = row.name
    if document_id:
        row = (
            await sess.execute(
                select(Document.filename, Document.project_id).where(
                    Document.id == document_id, Document.user_id == user_id
                )
            )
        ).first()
        if row is not None:
            out["current_document_name"] = row.filename
            if not out["current_project_name"]:
                proj = (
                    await sess.execute(
                        select(Project.name).where(Project.id == row.project_id)
                    )
                ).first()
                if proj is not None:
                    out["current_project_name"] = proj.name
    return out


async def _quota_remaining(
    sess: AsyncSession, user_id: str, role: str, user_quota: int
) -> int | None:
    if role not in ("admin", "super_admin"):
        return None
    if user_quota == 0:
        return None  # unlimited
    invited_count = (
        await sess.execute(
            select(func.count(UserProfile.id)).where(
                UserProfile.invited_by == user_id
            )
        )
    ).scalar() or 0
    return max(0, int(user_quota) - int(invited_count))


async def build_context_card(
    *,
    user_id: str,
    role: str,
    email: str,
    display_name: str | None,
    organization: str | None,
    project_id: str | None,
    document_id: str | None,
) -> dict[str, Any]:
    """Build a bounded JSON-able snapshot of the user's world.

    Single async session; ~5 small queries against indexed columns.
    Total payload size targets <1k tokens.
    """
    Session = get_session_factory()
    async with Session() as sess:
        # Profile already on the bearer claim — only need user_quota for admin.
        profile_row = (
            await sess.execute(
                select(UserProfile.user_quota).where(UserProfile.id == user_id)
            )
        ).first()
        user_quota = int(profile_row.user_quota) if profile_row is not None else 0

        scope = await _resolve_scope_names(sess, user_id, project_id, document_id)
        projects = await _project_doc_counts(sess, user_id)
        recent_documents = await _recent_documents(sess, user_id)
        in_progress_jobs = await _in_progress(sess, user_id)
        quota_remaining = await _quota_remaining(sess, user_id, role, user_quota)
        # Memory toggle gates BOTH global and project-scoped recall.
        memory_enabled = await get_memory_enabled(sess, user_id=user_id)
        if memory_enabled:
            # Strict isolation: project scope sees only project memories,
            # global scope sees only global memories.
            memories = await fetch_top_memories(
                sess, user_id=user_id, project_id=project_id,
            )
        else:
            memories = []

    project_count = len(projects)
    doc_count = sum(p["doc_count"] for p in projects)

    return {
        "user": {
            "email": email,
            "role": role,
            "organization": organization,
            "display_name": display_name,
        },
        "scope": scope,
        "catalog": {
            "project_count": project_count,
            "document_count": doc_count,
            "projects": projects,
            "recent_documents": recent_documents,
            "in_progress_jobs": in_progress_jobs,
        },
        "permissions": {
            "can_invite": role in ("admin", "super_admin"),
            "can_change_roles": role == "super_admin",
            "user_quota_remaining": quota_remaining,
        },
        "memories": memories,
    }


def is_corpus_empty(card: dict[str, Any]) -> bool:
    """True iff the user owns zero documents under the relevant scope."""
    catalog = card.get("catalog") or {}
    scope = card.get("scope") or {}
    if scope.get("current_document_id"):
        # If a doc is pinned, it exists (validated upstream); not empty.
        return False
    if scope.get("current_project_id"):
        target = scope.get("current_project_name")
        for p in catalog.get("projects") or []:
            if p.get("name") == target:
                return int(p.get("doc_count") or 0) == 0
        return True
    return int(catalog.get("document_count") or 0) == 0
