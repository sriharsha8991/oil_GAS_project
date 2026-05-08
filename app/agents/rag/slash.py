"""Slash commands — lightweight pre-router that intercepts ``/...`` messages
before they hit the RAG agent.

Supported commands (run only when the user message starts with a leading
``/``; everything else is passed through unchanged):

  /help                     — list available commands
  /clear                    — drop the current Redis session (STM wipe)
  /forget <key>             — forget one long-term memory by key
  /forget all               — wipe ALL long-term memories for the user
  /scope project=<uuid>     — re-scope this turn to a project (FE hint)
  /scope clear              — clear project scope for this turn (FE hint)
  /temp on | off            — toggle temp/incognito for this turn (FE hint)

Returns ``None`` when the message is not a slash command. Otherwise
returns a ``SlashResult`` the route uses to short-circuit ``/chat``
without calling the agent.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from app.agents.rag.sessions import delete_session_async
from app.db.engine import get_session_factory
from app.db.queries.memories import forget_all_memories, forget_memory
from app.utils.logging import get_logger

logger = get_logger(__name__)

_SLASH = re.compile(r"^\s*/(\w+)\b\s*(.*)$", re.DOTALL)
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


HELP_TEXT = (
    "Available slash commands:\n"
    "  /help — show this message\n"
    "  /clear — wipe the current conversation memory (this session only)\n"
    "  /forget <key> — forget one saved long-term memory\n"
    "  /forget all — wipe all your long-term memories\n"
    "  /scope project=<uuid> | /scope clear — set project scope (FE hint)\n"
    "  /temp on | /temp off — toggle incognito mode for this turn (FE hint)\n"
)


@dataclass
class SlashResult:
    """Outcome of a slash command. The route surfaces ``reply`` directly
    to the user and skips the RAG agent entirely.
    """

    reply: str
    status: str = "answered"  # always answered for slash commands
    meta: dict = field(default_factory=dict)


def parse(message: str) -> tuple[str, str] | None:
    """Return ``(command, args)`` if ``message`` starts with a slash."""
    if not message or not message.lstrip().startswith("/"):
        return None
    m = _SLASH.match(message)
    if not m:
        return None
    return m.group(1).lower(), (m.group(2) or "").strip()


async def handle(
    *,
    message: str,
    user_id: str,
    session_id: str,
) -> SlashResult | None:
    """Dispatch a slash command. Returns ``None`` when ``message`` is not one."""
    parsed = parse(message)
    if parsed is None:
        return None
    cmd, args = parsed

    if cmd == "help":
        return SlashResult(reply=HELP_TEXT, meta={"path": "slash", "cmd": "help"})

    if cmd == "clear":
        await delete_session_async(session_id)
        return SlashResult(
            reply="Conversation memory cleared for this session.",
            meta={"path": "slash", "cmd": "clear"},
        )

    if cmd == "forget":
        return await _handle_forget(args=args, user_id=user_id)

    if cmd == "scope":
        return _handle_scope(args)

    if cmd == "temp":
        return _handle_temp(args)

    return SlashResult(
        reply=f"Unknown command: /{cmd}. Type /help to see available commands.",
        meta={"path": "slash", "cmd": "unknown"},
    )


async def _handle_forget(*, args: str, user_id: str) -> SlashResult:
    args = args.strip()
    if not args:
        return SlashResult(
            reply="Usage: /forget <key> or /forget all",
            meta={"path": "slash", "cmd": "forget", "ok": False},
        )

    Session = get_session_factory()
    if args.lower() == "all":
        async with Session() as sess:
            removed = await forget_all_memories(sess, user_id=user_id)
            await sess.commit()
        return SlashResult(
            reply=f"Forgot {removed} long-term memories.",
            meta={"path": "slash", "cmd": "forget_all", "removed": removed},
        )

    # Treat the rest as a key. Iterate user scopes — caller doesn't know
    # which scope a key lives under, so we try them all.
    scopes = ("identity", "preference", "project")
    removed = 0
    async with Session() as sess:
        for scope in scopes:
            if await forget_memory(sess, user_id=user_id, scope=scope, key=args):
                removed += 1
        if removed:
            await sess.commit()
    if not removed:
        return SlashResult(
            reply=f"No saved memory found for key '{args}'.",
            meta={"path": "slash", "cmd": "forget", "removed": 0},
        )
    return SlashResult(
        reply=f"Forgot memory '{args}'.",
        meta={"path": "slash", "cmd": "forget", "removed": removed},
    )


def _handle_scope(args: str) -> SlashResult:
    args = args.strip()
    if args == "clear":
        return SlashResult(
            reply="Project scope cleared. Future messages will use your default scope.",
            meta={"path": "slash", "cmd": "scope_clear"},
        )
    if args.startswith("project="):
        candidate = args[len("project="):].strip()
        if not _UUID_RE.match(candidate):
            return SlashResult(
                reply="Invalid project id. Expected a UUID.",
                meta={"path": "slash", "cmd": "scope", "ok": False},
            )
        try:
            uuid.UUID(candidate)
        except ValueError:
            return SlashResult(
                reply="Invalid project id. Expected a UUID.",
                meta={"path": "slash", "cmd": "scope", "ok": False},
            )
        return SlashResult(
            reply=(
                "Project scope acknowledged. Send subsequent messages with "
                "project_id set on the request body to keep this scope."
            ),
            meta={"path": "slash", "cmd": "scope", "project_id": candidate},
        )
    return SlashResult(
        reply="Usage: /scope project=<uuid> or /scope clear",
        meta={"path": "slash", "cmd": "scope", "ok": False},
    )


def _handle_temp(args: str) -> SlashResult:
    a = args.strip().lower()
    if a == "on":
        return SlashResult(
            reply=(
                "Temporary chat acknowledged. Set ``temp: true`` on the next "
                "request body to keep the conversation incognito."
            ),
            meta={"path": "slash", "cmd": "temp", "value": True},
        )
    if a == "off":
        return SlashResult(
            reply=(
                "Temporary chat off acknowledged. Set ``temp: false`` (or omit) "
                "on the next request body."
            ),
            meta={"path": "slash", "cmd": "temp", "value": False},
        )
    return SlashResult(
        reply="Usage: /temp on or /temp off",
        meta={"path": "slash", "cmd": "temp", "ok": False},
    )
