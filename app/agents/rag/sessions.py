"""Redis-backed STM (short-term memory) for chat sessions.

Replaces the in-process ``defaultdict[ChatSession]`` with Redis so session
state survives uvicorn worker restarts and is shared across workers.

- Key:     ``chat:session:{session_id}``
- Value:   JSON-encoded ChatSession snapshot
- TTL:     24h sliding (refreshed on every read/write)

Falls back to the in-process dict when Redis is unreachable so chat keeps
working in degraded mode.
"""

from __future__ import annotations

import json
import os
from typing import Any

import redis.asyncio as aioredis

from app.agents.rag.types import ChatMessage, ChatSession, _sessions
from app.utils.logging import get_logger

logger = get_logger(__name__)

_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/2")
_KEY_PREFIX = "chat:session:"
_TTL_SECONDS = 24 * 3600

_client_singleton: aioredis.Redis | None = None


def _client() -> aioredis.Redis:
    global _client_singleton  # noqa: PLW0603
    if _client_singleton is None:
        _client_singleton = aioredis.from_url(_REDIS_URL, decode_responses=True)
    return _client_singleton


def _serialize(session: ChatSession) -> str:
    return json.dumps({
        "messages": [{"role": m.role, "content": m.content} for m in session.messages],
        "summary": session.summary,
        "current_subject": session.current_subject,
    })


def _deserialize(raw: str) -> ChatSession:
    data: dict[str, Any] = json.loads(raw)
    msgs = [
        ChatMessage(role=m["role"], content=m["content"])
        for m in (data.get("messages") or [])
    ]
    return ChatSession(
        messages=msgs,
        summary=data.get("summary") or "",
        current_subject=data.get("current_subject") or {},
    )


async def get_session_async(session_id: str) -> ChatSession:
    """Load a session from Redis, falling back to in-process cache."""
    try:
        r = _client()
        raw = await r.get(f"{_KEY_PREFIX}{session_id}")
        if raw:
            await r.expire(f"{_KEY_PREFIX}{session_id}", _TTL_SECONDS)
            return _deserialize(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("session_redis_get_failed", error=str(exc), session_id=session_id)
        return _sessions[session_id]
    return ChatSession()


async def save_session_async(session_id: str, session: ChatSession) -> None:
    """Persist a session to Redis with a sliding 24h TTL."""
    try:
        r = _client()
        await r.set(
            f"{_KEY_PREFIX}{session_id}",
            _serialize(session),
            ex=_TTL_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("session_redis_set_failed", error=str(exc), session_id=session_id)
        _sessions[session_id] = session


async def delete_session_async(session_id: str) -> None:
    """Drop a session entirely (used by ``/clear`` slash-command)."""
    try:
        r = _client()
        await r.delete(f"{_KEY_PREFIX}{session_id}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("session_redis_del_failed", error=str(exc), session_id=session_id)
    _sessions.pop(session_id, None)
