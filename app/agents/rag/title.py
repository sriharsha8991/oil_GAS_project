"""Auto-generate a short title for a chat session from its first turn.

Mirrors the ChatGPT sidebar UX: after the first answered turn, run a tiny
Flash call to summarize the conversation into a 3-8 word title. Falls back
to a truncation of the first user message when the LLM call fails or
returns nothing usable.

Best-effort and fire-and-forget — never raises into the request path.
"""

from __future__ import annotations

import re

from google import genai
from google.genai import types

from app.config import Settings
from app.utils.logging import get_logger

logger = get_logger(__name__)


_TITLE_SYSTEM = """\
You write short titles for a chat sidebar. Read the user's first question \
and the assistant's first answer, then produce ONE concise title of 3 to \
8 words. Plain text, no quotes, no trailing punctuation, Title Case. \
Capture the topic — not pleasantries. If the topic is unclear, output \
"New chat".
"""


_TITLE_MAX_CHARS = 80
_TITLE_BAD_RE = re.compile(r"^[\s\-_.,:;\"']+|[\s\-_.,:;\"']+$")


def _fallback_title(user_message: str) -> str:
    """Truncate the first user message into a usable title."""
    text = (user_message or "").strip().replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    if not text:
        return "New chat"
    if len(text) > 60:
        text = text[:57].rstrip() + "…"
    return text


def _clean(title: str) -> str:
    lines = (title or "").strip().splitlines()
    if not lines:
        return ""
    title = lines[0].strip()
    title = _TITLE_BAD_RE.sub("", title)
    if len(title) > _TITLE_MAX_CHARS:
        title = title[:_TITLE_MAX_CHARS].rstrip() + "…"
    return title


async def generate_title(
    *,
    user_message: str,
    assistant_message: str,
    settings: Settings,
    client: genai.Client | None = None,
) -> str:
    """Return a short title; never raises."""
    if not user_message:
        return "New chat"

    cli = client or genai.Client(api_key=settings.gemini_api_key)
    prompt = (
        f"User: {user_message[:600]}\n\n"
        f"Assistant: {(assistant_message or '')[:600]}"
    )
    try:
        resp = await cli.aio.models.generate_content(
            model=settings.chat_synth_model,
            contents=[types.Content(
                role="user", parts=[types.Part.from_text(text=prompt)],
            )],
            config=types.GenerateContentConfig(
                system_instruction=_TITLE_SYSTEM,
                temperature=0.2,
                max_output_tokens=64,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
        raw = (getattr(resp, "text", "") or "").strip()
        cleaned = _clean(raw)
        if cleaned and cleaned.lower() != "new chat":
            return cleaned
    except Exception as exc:  # noqa: BLE001
        logger.warning("chat_title_gen_failed", error=str(exc))
    return _fallback_title(user_message)
