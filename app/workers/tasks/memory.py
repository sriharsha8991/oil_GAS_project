"""Celery task: passive long-term memory capture.

Fires after every answered chat turn (synthesizer path only). Asks Flash to
extract durable preferences / facts / project pins / style cues from the
turn and upserts them into ``public.user_memories``.

Best-effort. Any failure is logged and the task ends — the user's chat is
unaffected.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.workers.celery_app import celery_app

# Gate thresholds — keep cheap, drop low-signal turns.
MIN_USER_WORDS = 6
MIN_ASSISTANT_CHARS = 60


_EXTRACT_SYSTEM = """\
You are a memory extractor for a domain assistant. Read ONE chat turn (the \
user's message + the assistant's answer) and extract DURABLE items the \
assistant should remember about THIS user across future sessions.

Output a strict JSON list (no prose, no fences). Each item:
  {"scope": "preference"|"fact"|"project_pin"|"style", "key": "...", "value": "..."}

Scope guidance:
  preference  — stable preferences ("always uses metric units", "prefers concise replies")
  fact        — durable user facts ("is a drilling engineer at PetroX")
  project_pin — pinned default project / asset ("default project is Sajaa")
  style       — tone / length / formality cues ("prefers terse technical answers")

Hard rules:
  • Return [] if nothing durable was expressed. Most turns return [].
  • Do NOT extract one-off questions or document content as memories.
  • Keys are short, lowercase, snake_case (e.g. "units", "default_project").
  • Values are short strings (≤ 80 chars).
"""


async def _run_extract(
    user_id: str, user_msg: str, assistant_msg: str,
) -> list[dict[str, Any]]:
    """Single Flash call returning the extracted memory list."""
    from google import genai
    from google.genai import types

    from app.config import get_settings
    from app.utils.logging import get_logger

    log = get_logger(__name__)
    settings = get_settings()
    client = genai.Client(api_key=settings.gemini_api_key)
    user_block = (
        f"User message:\n{user_msg[:1500]}\n\n"
        f"Assistant answer:\n{assistant_msg[:2500]}"
    )
    try:
        resp = await client.aio.models.generate_content(
            model=settings.chat_synth_model,
            contents=[types.Content(
                role="user", parts=[types.Part.from_text(text=user_block)],
            )],
            config=types.GenerateContentConfig(
                system_instruction=_EXTRACT_SYSTEM,
                temperature=0.0,
                max_output_tokens=400,
                response_mime_type="application/json",
            ),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("memory_extract_llm_error", error=str(exc), user_id=user_id)
        return []

    text = (getattr(resp, "text", "") or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        log.warning("memory_extract_bad_json", text_head=text[:200])
        return []

    items: list[dict[str, Any]] = []
    if isinstance(parsed, list):
        for raw in parsed:
            if not isinstance(raw, dict):
                continue
            scope = (raw.get("scope") or "").strip().lower()
            key = (raw.get("key") or "").strip().lower().replace(" ", "_")
            value = raw.get("value")
            if not scope or not key or value in (None, ""):
                continue
            items.append({"scope": scope, "key": key[:120], "value": value})
    return items


async def _persist(
    user_id: str,
    items: list[dict[str, Any]],
    source_msg: str,
    project_id: str | None,
) -> int:
    from app.db.engine import get_session_factory
    from app.db.queries.memories import MEMORY_SCOPES, upsert_memory
    from app.utils.logging import get_logger

    log = get_logger(__name__)
    written = 0
    Session = get_session_factory()
    async with Session() as sess:
        for it in items:
            if it["scope"] not in MEMORY_SCOPES or it["scope"] == "dismissed":
                continue
            try:
                await upsert_memory(
                    sess,
                    user_id=user_id,
                    project_id=project_id,
                    scope=it["scope"],
                    key=it["key"],
                    value=it["value"],
                    source_msg=source_msg,
                )
                written += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("memory_upsert_failed",
                            error=str(exc), scope=it["scope"], key=it["key"])
        if written:
            await sess.commit()
    return written


async def _memory_enabled_for(user_id: str) -> bool:
    """Look up the user's toggle. Default true on lookup failure (best-effort)."""
    from app.db.engine import get_session_factory
    from app.db.queries.memories import get_memory_enabled
    from app.utils.logging import get_logger

    log = get_logger(__name__)
    try:
        Session = get_session_factory()
        async with Session() as sess:
            return await get_memory_enabled(sess, user_id=user_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("memory_toggle_lookup_failed",
                    user_id=user_id, error=str(exc))
        return True


@celery_app.task(
    name="extract_user_memory",
    queue="default",
    acks_late=True,
    max_retries=0,
)
def extract_user_memory(
    user_id: str,
    user_msg: str,
    assistant_msg: str,
    session_id: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Best-effort capture. Returns ``{"captured": N}`` for observability.

    Honors the per-user ``memory_enabled`` toggle (no-op when disabled)
    and tags captured rows with ``project_id`` for strict isolation.
    """
    from app.utils.logging import get_logger

    log = get_logger(__name__)
    if not user_id or not user_msg or not assistant_msg:
        return {"captured": 0, "skipped": "empty"}
    if len(user_msg.split()) < MIN_USER_WORDS:
        return {"captured": 0, "skipped": "user_too_short"}
    if len(assistant_msg) < MIN_ASSISTANT_CHARS:
        return {"captured": 0, "skipped": "assistant_too_short"}

    try:
        if not asyncio.run(_memory_enabled_for(user_id)):
            return {"captured": 0, "skipped": "memory_disabled"}
        items = asyncio.run(_run_extract(user_id, user_msg, assistant_msg))
        if not items:
            return {"captured": 0}
        written = asyncio.run(
            _persist(user_id, items, user_msg[:500], project_id)
        )
        log.info("memory_capture_done",
                 user_id=user_id, captured=written,
                 project_id=project_id, session_id=session_id)
        return {"captured": written}
    except Exception as exc:  # noqa: BLE001
        log.error("memory_capture_failed",
                  exc_info=True, user_id=user_id, error=str(exc))
        return {"captured": 0, "error": str(exc)}
