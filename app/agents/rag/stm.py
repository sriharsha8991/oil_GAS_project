"""STM background tasks — rolling summary + sticky subject extraction.

Both run after the user has been answered, so they don't add latency to the
visible response. They are best-effort: any failure is logged and swallowed.
"""

from __future__ import annotations

import json
from typing import Any

from google import genai
from google.genai import types

from app.agents.rag.types import MAX_HISTORY_TURNS, ChatMessage, ChatSession
from app.config import Settings
from app.utils.logging import get_logger

logger = get_logger(__name__)

# Compress when the live message buffer reaches the cap. We summarize the
# oldest 6 messages and replace them with one synthetic system entry.
SUMMARY_COMPRESS_AT = MAX_HISTORY_TURNS  # 10 messages
SUMMARY_COMPRESS_KEEP = 6                # keep newest 6 verbatim


_SUMMARY_SYSTEM = """\
You compress chat history. Output ONE short paragraph (≤120 tokens) that \
preserves: the user's intent, named entities (wells, documents, formations, \
depths, runs), and any decisions/answers from the assistant. No filler. \
Plain text only.
"""


_SUBJECT_SYSTEM = """\
Extract the primary subject from the assistant's latest answer. \
Output strict JSON with these keys (omit any that don't apply): \
{"well": "...", "document": "...", "formation": "...", "depth_datum": "...", "run": "..."}. \
Truncate every value to 80 chars. If nothing is identifiable, output {}.
"""


async def maybe_compress_history(
    *,
    session: ChatSession,
    settings: Settings,
    client: genai.Client,
) -> None:
    """Replace older messages with a rolling summary when the cap is hit."""
    if len(session.messages) < SUMMARY_COMPRESS_AT:
        return
    keep = session.messages[-SUMMARY_COMPRESS_KEEP:]
    older = session.messages[:-SUMMARY_COMPRESS_KEEP]
    if not older:
        return

    history_text = "\n".join(f"{m.role}: {m.content}" for m in older)
    prefix = (
        f"Previous summary (carry forward):\n{session.summary}\n\n"
        if session.summary else ""
    )
    user_text = f"{prefix}New turns to compress:\n{history_text}"

    try:
        resp = await client.aio.models.generate_content(
            model=settings.chat_synth_model,
            contents=[types.Content(
                role="user", parts=[types.Part.from_text(text=user_text)],
            )],
            config=types.GenerateContentConfig(
                system_instruction=_SUMMARY_SYSTEM,
                temperature=0.1,
                max_output_tokens=200,
            ),
        )
        new_summary = (getattr(resp, "text", "") or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("stm_summary_failed", error=str(exc))
        return

    if not new_summary:
        return
    session.summary = new_summary
    session.messages = keep
    logger.info("stm_summary_compressed",
                kept=len(keep), summary_len=len(new_summary))


async def update_current_subject(
    *,
    session: ChatSession,
    user_message: str,
    assistant_message: str,
    settings: Settings,
    client: genai.Client,
) -> None:
    """Refresh ``session.current_subject`` from the latest answered turn."""
    if not assistant_message:
        return
    user_text = (
        f"User asked:\n{user_message}\n\n"
        f"Assistant answered:\n{assistant_message[:1500]}"
    )
    try:
        resp = await client.aio.models.generate_content(
            model=settings.chat_synth_model,
            contents=[types.Content(
                role="user", parts=[types.Part.from_text(text=user_text)],
            )],
            config=types.GenerateContentConfig(
                system_instruction=_SUBJECT_SYSTEM,
                temperature=0.0,
                max_output_tokens=120,
                response_mime_type="application/json",
            ),
        )
        text = (getattr(resp, "text", "") or "").strip()
        if not text:
            return
        parsed: dict[str, Any] = json.loads(text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("stm_subject_failed", error=str(exc))
        return

    cleaned: dict[str, str] = {}
    for k, v in parsed.items():
        if not isinstance(v, str):
            continue
        v = v.strip()
        if v:
            cleaned[k] = v[:80]
    # Merge: new keys overwrite, missing keys keep prior value (carry-over).
    if cleaned:
        merged = {**session.current_subject, **cleaned}
        session.current_subject = merged
        logger.info("stm_subject_updated", keys=sorted(cleaned.keys()))


def render_subject_line(subject: dict[str, str]) -> str:
    """Format current_subject as a one-line block for the guard prompt."""
    if not subject:
        return ""
    parts = [f"{k}={v}" for k, v in subject.items() if v]
    return "; ".join(parts)


def render_history_block(session: ChatSession, max_recent: int = 6) -> str:
    """Render history (rolling summary + last N messages) for prompts."""
    parts: list[str] = []
    if session.summary:
        parts.append(f"summary: {session.summary}")
    recent: list[ChatMessage] = session.messages[-max_recent:]
    parts.extend(f"{m.role}: {m.content}" for m in recent)
    return "\n".join(parts)
