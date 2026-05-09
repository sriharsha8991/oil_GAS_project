"""In-process chat probe — runs the streaming agent against the user's exact
query and prints every event with timing. Picks the most-recent ingested
document for the most-recent user. Safe / read-only.

Run:  python -m app.tools.probe_chat
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

from app.agents.rag import run_agent_stream
from app.config import get_settings
from app.db.queries.context_card import build_context_card
from app.vectorstore.embedder import GeminiEmbedder
from app.vectorstore.qdrant_store import QdrantVectorStore

QUERY = (
    "Im Sriharsha Drilling Specilist from sanfrasico tell me the NPT "
    "details bore details"
)


def _to_asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _pick_user_and_doc(dsn: str) -> tuple[str, str | None, str | None]:
    """Find a user with at least one succeeded document; return (user_id,
    document_id, project_id). Prefers the user behind session_id from logs."""
    target_session = "3b715fad-7a6b-4377-ba83-ce9a1c715402"
    conn = await asyncpg.connect(_to_asyncpg_dsn(dsn))
    try:
        row = await conn.fetchrow(
            "select user_id from public.chat_sessions where id = $1",
            target_session,
        )
        if row:
            user_id = str(row["user_id"])
        else:
            row = await conn.fetchrow(
                "select id from auth.users order by created_at desc limit 1"
            )
            user_id = str(row["id"]) if row else None
        if not user_id:
            return "", None, None

        doc_row = await conn.fetchrow(
            "select d.id::text as id, d.project_id::text as project_id, "
            "d.filename "
            "from public.documents d "
            "join public.jobs j on j.document_id = d.id "
            "where d.user_id = $1 and j.status in ('succeeded','completed') "
            "order by d.created_at desc limit 1",
            user_id,
        )
        if doc_row:
            print(f"[probe] using doc {doc_row['filename']} ({doc_row['id']})")
            return user_id, doc_row["id"], doc_row["project_id"]
        return user_id, None, None
    finally:
        await conn.close()


async def main() -> int:
    load_dotenv()
    dsn = os.environ["DATABASE_URL"]
    settings = get_settings()

    user_id, document_id, project_id = await _pick_user_and_doc(dsn)
    if not user_id:
        print("[probe] no user found")
        return 1
    print(f"[probe] user_id={user_id}")
    print(f"[probe] doc_id={document_id} project_id={project_id}")
    print(f"[probe] query = {QUERY!r}")

    embedder = GeminiEmbedder(settings)
    vector_store = QdrantVectorStore(settings)

    try:
        card = await build_context_card(
            user_id=user_id, role="drilling_engineer",
            email=None, display_name=None, organization=None,
            project_id=project_id, document_id=document_id,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[probe] context card failed: {exc!r}")
        card = {}

    session_id = str(uuid.uuid4())
    print(f"[probe] session_id={session_id}\n")
    print("=" * 70)

    t0 = time.monotonic()
    last_t = t0
    token_count = 0
    answer_buf: list[str] = []
    done_payload: dict | None = None
    event_log: list[dict] = []

    async for ev in run_agent_stream(
        QUERY,
        session_id=session_id,
        settings=settings,
        embedder=embedder,
        vector_store=vector_store,
        user_id=user_id,
        card=card,
        project_id=project_id,
        document_id=document_id,
        temp=True,  # don't pollute history
    ):
        now = time.monotonic()
        dt = (now - last_t) * 1000
        elapsed = (now - t0) * 1000
        last_t = now
        kind = ev.get("event")
        # Capture every frame verbatim with timing for the JSON dump.
        event_log.append({
            "seq": len(event_log),
            "elapsed_ms": round(elapsed, 2),
            "delta_ms": round(dt, 2),
            "wall_ts": datetime.now(timezone.utc).isoformat(),
            "event": kind,
            "payload": ev,
        })
        if kind == "status":
            phase = ev.get("phase")
            extra = {k: v for k, v in ev.items() if k not in ("event", "phase")}
            print(f"[+{elapsed:7.0f}ms Δ{dt:6.0f}ms] STATUS phase={phase} {extra or ''}")
        elif kind == "token":
            token_count += 1
            text = ev.get("text", "")
            answer_buf.append(text)
            preview = text.replace("\n", "\\n")[:80]
            print(f"[+{elapsed:7.0f}ms Δ{dt:6.0f}ms] TOKEN #{token_count:3d} len={len(text):4d} {preview!r}")
        elif kind == "done":
            done_payload = ev
            print(f"[+{elapsed:7.0f}ms Δ{dt:6.0f}ms] DONE status={ev.get('status')}")
        else:
            print(f"[+{elapsed:7.0f}ms Δ{dt:6.0f}ms] ??? {kind} {ev}")

    print("=" * 70)
    full_concat = "".join(answer_buf)
    print(f"\n[probe] tokens={token_count}  concat_len={len(full_concat)}")
    if done_payload:
        ans = done_payload.get("answer") or ""
        meta = done_payload.get("meta") or {}
        sources = done_payload.get("sources") or []
        followups = done_payload.get("followups") or []
        print(f"[probe] done.status         = {done_payload.get('status')!r}")
        print(f"[probe] done.answer length  = {len(ans)}")
        print(f"[probe] done.answer preview = {ans[:200]!r}")
        print(f"[probe] sources count       = {len(sources)}")
        print(f"[probe] followups           = {followups}")
        print(f"[probe] meta.path           = {meta.get('path')}")
        print(f"[probe] meta.empty_response = {meta.get('empty_response')}")
        print(f"[probe] meta.rag_calls      = {meta.get('rag_calls')}")
        print(f"[probe] meta.total_hits     = {meta.get('total_hits')}")
        print(f"[probe] meta.verification   = {meta.get('verification')}")
        match = "EQUAL" if ans == full_concat else (
            "ANSWER LONGER" if len(ans) > len(full_concat) else "CONCAT LONGER"
        )
        print(f"[probe] answer vs concat    = {match}")

    out_dir = Path("data") / "probe_runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"probe_{stamp}_{session_id[:8]}.json"
    dump = {
        "query": QUERY,
        "session_id": session_id,
        "user_id": user_id,
        "document_id": document_id,
        "project_id": project_id,
        "started_at": datetime.fromtimestamp(
            time.time() - (time.monotonic() - t0), tz=timezone.utc
        ).isoformat(),
        "wall_ms": round((time.monotonic() - t0) * 1000, 2),
        "token_count": token_count,
        "concat_len": len(full_concat),
        "concat": full_concat,
        "done": done_payload,
        "events": event_log,
    }
    out_path.write_text(json.dumps(dump, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\n[probe] wrote {len(event_log)} events -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
