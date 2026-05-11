"""Slice 2 tests for support tickets — admin triage, notes, status updates,
soft-delete, rate limiting, and the user-update email helper.

Same approach as test_support_tickets.py: mount the router(s) on a bare
FastAPI app, override `current_user` / `require_role`, and mock DB +
storage + email + redis at the module boundary.
"""

from __future__ import annotations

import io
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.deps import current_user, require_role
from app.auth.middleware import AuthUser
from app.routes.admin_support import router as admin_router
from app.routes.support import router as support_router


ADMIN_ID = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())


def _user(role: str = "drilling_engineer", uid: str = USER_ID) -> AuthUser:
    return AuthUser(
        id=uid, email=f"{uid[:6]}@example.com", role=role,
        display_name="Display", organization="Org",
    )


def _build_admin_app(user: AuthUser) -> FastAPI:
    app = FastAPI()
    app.include_router(admin_router)
    app.dependency_overrides[current_user] = lambda: user
    # The router-level dep + the per-route dep both call require_role("super_admin");
    # override every variant by class identity (FastAPI keys overrides on the
    # actual callable object).
    for route in app.routes:
        for d in getattr(route, "dependencies", []) or []:
            app.dependency_overrides[d.dependency] = lambda u=user: u
        if hasattr(route, "dependant"):
            for sub in route.dependant.dependencies:
                if sub.call is not None and sub.call.__name__ == "_dep":
                    app.dependency_overrides[sub.call] = lambda u=user: u
    return app


def _build_support_app(user: AuthUser) -> FastAPI:
    app = FastAPI()
    app.include_router(support_router)
    app.dependency_overrides[current_user] = lambda: user
    return app


def _session_factory_mock():
    sess = MagicMock()
    sess.commit = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=sess)
    cm.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=cm), sess


# ---------------------------------------------------------------------------
# Admin authorization gate
# ---------------------------------------------------------------------------


class TestAdminAuthGate:
    def test_non_super_admin_gets_403(self):
        # Use a real require_role dep — only override current_user.
        app = FastAPI()
        app.include_router(admin_router)
        app.dependency_overrides[current_user] = lambda: _user(role="drilling_engineer")

        # Inject the user into request.state so require_role can read it.
        @app.middleware("http")
        async def _inject(request, call_next):
            request.state.user = _user(role="drilling_engineer")
            return await call_next(request)

        with TestClient(app) as client:
            r = client.get("/admin/support/tickets")
        assert r.status_code == 403
        assert r.json()["detail"]["error_code"] == "requires_role"

    def test_super_admin_passes_gate(self):
        app = FastAPI()
        app.include_router(admin_router)

        @app.middleware("http")
        async def _inject(request, call_next):
            request.state.user = _user(role="super_admin", uid=ADMIN_ID)
            return await call_next(request)

        with patch("app.routes.admin_support.list_admin_tickets", new_callable=AsyncMock) as mock_list, \
             patch("app.routes.admin_support.get_session_factory") as mock_factory:
            mock_list.return_value = ([], None, {"open": 0, "in_progress": 0, "resolved": 0, "closed": 0})
            factory, _sess = _session_factory_mock()
            mock_factory.return_value = factory
            with TestClient(app) as client:
                r = client.get("/admin/support/tickets")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# GET /admin/support/tickets  (list)
# ---------------------------------------------------------------------------


class TestAdminList:
    @patch("app.routes.admin_support.list_admin_tickets", new_callable=AsyncMock)
    @patch("app.routes.admin_support.get_session_factory")
    def test_returns_items_counts_cursor(self, mock_factory, mock_list):
        mock_list.return_value = (
            [
                {
                    "ticket_id": "t1", "category": "bug", "subject": "S",
                    "status": "open",
                    "user": {"id": USER_ID, "email": "u@example.com", "name": "U"},
                    "attachment_count": 1,
                    "created_at": "2026-05-11T10:00:00+00:00",
                    "updated_at": "2026-05-11T10:00:00+00:00",
                }
            ],
            "cur-x",
            {"open": 5, "in_progress": 2, "resolved": 1, "closed": 0},
        )
        factory, _ = _session_factory_mock()
        mock_factory.return_value = factory
        app = _build_admin_app(_user(role="super_admin", uid=ADMIN_ID))
        with TestClient(app) as client:
            r = client.get("/admin/support/tickets?limit=10&status=open&category=bug&q=foo&user_id=" + USER_ID)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["next_cursor"] == "cur-x"
        assert body["counts"]["open"] == 5
        assert body["items"][0]["user"]["email"] == "u@example.com"

        kwargs = mock_list.call_args.kwargs
        assert kwargs["limit"] == 10
        assert kwargs["status"] == "open"
        assert kwargs["category"] == "bug"
        assert kwargs["user_id"] == USER_ID
        assert kwargs["q"] == "foo"

    def test_invalid_status_400(self):
        app = _build_admin_app(_user(role="super_admin", uid=ADMIN_ID))
        with TestClient(app) as client:
            r = client.get("/admin/support/tickets?status=bogus")
        assert r.status_code == 400
        assert r.json()["detail"]["error_code"] == "invalid_status"

    def test_invalid_category_400(self):
        app = _build_admin_app(_user(role="super_admin", uid=ADMIN_ID))
        with TestClient(app) as client:
            r = client.get("/admin/support/tickets?category=spam")
        assert r.status_code == 400
        assert r.json()["detail"]["error_code"] == "invalid_category"


# ---------------------------------------------------------------------------
# GET /admin/support/tickets/{id}
# ---------------------------------------------------------------------------


class TestAdminDetail:
    @patch("app.routes.admin_support.get_admin_ticket", new_callable=AsyncMock)
    @patch("app.routes.admin_support.get_session_factory")
    def test_returns_full_detail(self, mock_factory, mock_get):
        mock_get.return_value = {
            "ticket_id": "abc", "category": "bug", "subject": "S",
            "message": "M", "status": "open", "diagnostics": None,
            "email_sent": True,
            "user": {"id": USER_ID, "email": "u@example.com", "name": "U"},
            "attachments": [], "notes": [],
            "created_at": "2026-05-11T10:00:00+00:00",
            "updated_at": "2026-05-11T10:00:00+00:00",
        }
        factory, _ = _session_factory_mock()
        mock_factory.return_value = factory
        app = _build_admin_app(_user(role="super_admin", uid=ADMIN_ID))
        with TestClient(app) as client:
            r = client.get("/admin/support/tickets/abc")
        assert r.status_code == 200
        body = r.json()
        assert body["user"]["id"] == USER_ID

    @patch("app.routes.admin_support.get_admin_ticket", new_callable=AsyncMock)
    @patch("app.routes.admin_support.get_session_factory")
    def test_unknown_404(self, mock_factory, mock_get):
        mock_get.return_value = None
        factory, _ = _session_factory_mock()
        mock_factory.return_value = factory
        app = _build_admin_app(_user(role="super_admin", uid=ADMIN_ID))
        with TestClient(app) as client:
            r = client.get("/admin/support/tickets/xyz")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /admin/support/tickets/{id}
# ---------------------------------------------------------------------------


class TestAdminPatch:
    def _settings(self, notify=True):
        return MagicMock(support_notify_user_on_update=notify)

    def _ticket_owner(self, status="open", subject="S", email="u@example.com"):
        ticket = MagicMock()
        ticket.id = uuid.uuid4()
        ticket.status = status
        ticket.subject = subject
        owner = MagicMock()
        owner.email = email
        owner.display_name = "U"
        return ticket, owner

    @patch("app.routes.admin_support.send_support_ticket_update_email", new_callable=AsyncMock)
    @patch("app.routes.admin_support.update_ticket_status", new_callable=AsyncMock)
    @patch("app.routes.admin_support.add_admin_note", new_callable=AsyncMock)
    @patch("app.routes.admin_support.get_ticket_with_owner", new_callable=AsyncMock)
    @patch("app.routes.admin_support.get_session_factory")
    @patch("app.routes.admin_support.get_settings")
    def test_status_change_sends_email(
        self, mock_settings, mock_factory, mock_get, mock_note, mock_status, mock_email,
    ):
        mock_settings.return_value = self._settings(notify=True)
        mock_get.return_value = self._ticket_owner()
        mock_email.return_value = "msg-xyz"
        factory, _ = _session_factory_mock()
        mock_factory.return_value = factory

        app = _build_admin_app(_user(role="super_admin", uid=ADMIN_ID))
        with TestClient(app) as client:
            r = client.patch("/admin/support/tickets/abc", json={"status": "resolved"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "resolved"
        assert body["note_added"] is False
        assert body["user_notified"] is True

        mock_status.assert_awaited_once()
        assert mock_status.call_args.kwargs["status"] == "resolved"
        mock_note.assert_not_awaited()
        # Email kwargs
        ek = mock_email.call_args.kwargs
        assert ek["new_status"] == "resolved"
        assert ek["note_text"] is None
        assert ek["user_email"] == "u@example.com"

    @patch("app.routes.admin_support.send_support_ticket_update_email", new_callable=AsyncMock)
    @patch("app.routes.admin_support.update_ticket_status", new_callable=AsyncMock)
    @patch("app.routes.admin_support.add_admin_note", new_callable=AsyncMock)
    @patch("app.routes.admin_support.get_ticket_with_owner", new_callable=AsyncMock)
    @patch("app.routes.admin_support.get_session_factory")
    @patch("app.routes.admin_support.get_settings")
    def test_note_only_inserts_note(
        self, mock_settings, mock_factory, mock_get, mock_note, mock_status, mock_email,
    ):
        mock_settings.return_value = self._settings(notify=False)
        mock_get.return_value = self._ticket_owner(status="open")
        factory, _ = _session_factory_mock()
        mock_factory.return_value = factory

        app = _build_admin_app(_user(role="super_admin", uid=ADMIN_ID))
        with TestClient(app) as client:
            r = client.patch("/admin/support/tickets/abc", json={"note": "looking into it"})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "open"
        assert body["note_added"] is True
        assert body["user_notified"] is False  # notify flag off

        mock_note.assert_awaited_once()
        assert mock_note.call_args.kwargs["text"] == "looking into it"
        assert mock_note.call_args.kwargs["author_user_id"] == ADMIN_ID
        mock_status.assert_not_awaited()
        mock_email.assert_not_awaited()

    @patch("app.routes.admin_support.get_ticket_with_owner", new_callable=AsyncMock)
    @patch("app.routes.admin_support.get_session_factory")
    def test_empty_body_400(self, mock_factory, mock_get):
        factory, _ = _session_factory_mock()
        mock_factory.return_value = factory
        app = _build_admin_app(_user(role="super_admin", uid=ADMIN_ID))
        with TestClient(app) as client:
            r = client.patch("/admin/support/tickets/abc", json={})
        assert r.status_code == 400
        assert r.json()["detail"] == "status_or_note_required"

    @patch("app.routes.admin_support.get_ticket_with_owner", new_callable=AsyncMock)
    @patch("app.routes.admin_support.get_session_factory")
    def test_invalid_status_400(self, mock_factory, mock_get):
        factory, _ = _session_factory_mock()
        mock_factory.return_value = factory
        app = _build_admin_app(_user(role="super_admin", uid=ADMIN_ID))
        with TestClient(app) as client:
            r = client.patch("/admin/support/tickets/abc", json={"status": "bogus"})
        assert r.status_code == 400
        assert r.json()["detail"]["error_code"] == "invalid_status"

    @patch("app.routes.admin_support.get_ticket_with_owner", new_callable=AsyncMock)
    @patch("app.routes.admin_support.get_session_factory")
    def test_unknown_ticket_404(self, mock_factory, mock_get):
        mock_get.return_value = None
        factory, _ = _session_factory_mock()
        mock_factory.return_value = factory
        app = _build_admin_app(_user(role="super_admin", uid=ADMIN_ID))
        with TestClient(app) as client:
            r = client.patch("/admin/support/tickets/abc", json={"status": "open"})
        assert r.status_code == 404

    @patch("app.routes.admin_support.send_support_ticket_update_email", new_callable=AsyncMock)
    @patch("app.routes.admin_support.update_ticket_status", new_callable=AsyncMock)
    @patch("app.routes.admin_support.get_ticket_with_owner", new_callable=AsyncMock)
    @patch("app.routes.admin_support.get_session_factory")
    @patch("app.routes.admin_support.get_settings")
    def test_email_failure_still_returns_200_with_user_notified_false(
        self, mock_settings, mock_factory, mock_get, mock_status, mock_email,
    ):
        mock_settings.return_value = self._settings(notify=True)
        mock_get.return_value = self._ticket_owner()
        mock_email.side_effect = RuntimeError("resend down")
        factory, _ = _session_factory_mock()
        mock_factory.return_value = factory

        app = _build_admin_app(_user(role="super_admin", uid=ADMIN_ID))
        with TestClient(app) as client:
            r = client.patch("/admin/support/tickets/abc", json={"status": "closed"})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "closed"
        assert body["user_notified"] is False


# ---------------------------------------------------------------------------
# DELETE /admin/support/tickets/{id}
# ---------------------------------------------------------------------------


class TestAdminDelete:
    @patch("app.routes.admin_support.soft_delete_ticket", new_callable=AsyncMock)
    @patch("app.routes.admin_support.get_session_factory")
    def test_soft_delete_ok(self, mock_factory, mock_del):
        mock_del.return_value = True
        factory, _ = _session_factory_mock()
        mock_factory.return_value = factory
        app = _build_admin_app(_user(role="super_admin", uid=ADMIN_ID))
        with TestClient(app) as client:
            r = client.delete("/admin/support/tickets/abc")
        assert r.status_code == 200
        assert r.json() == {"ticket_id": "abc", "deleted": True}

    @patch("app.routes.admin_support.soft_delete_ticket", new_callable=AsyncMock)
    @patch("app.routes.admin_support.get_session_factory")
    def test_unknown_404(self, mock_factory, mock_del):
        mock_del.return_value = False
        factory, _ = _session_factory_mock()
        mock_factory.return_value = factory
        app = _build_admin_app(_user(role="super_admin", uid=ADMIN_ID))
        with TestClient(app) as client:
            r = client.delete("/admin/support/tickets/abc")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Rate limit on POST /support/tickets
# ---------------------------------------------------------------------------


class TestRateLimit:
    @patch("app.routes.support.enforce_support_submission", new_callable=AsyncMock)
    def test_429_when_rate_limited(self, mock_rl):
        from app.utils.rate_limit import RateLimitDecision
        mock_rl.return_value = RateLimitDecision(
            allowed=False, retry_after_seconds=42, scope="rl:support:hour:x",
        )
        app = _build_support_app(_user())
        with TestClient(app) as client:
            r = client.post(
                "/support/tickets",
                data={"category": "bug", "subject": "x", "message": "x"},
            )
        assert r.status_code == 429
        assert r.json()["detail"]["error_code"] == "rate_limited"
        assert r.json()["detail"]["retry_after_seconds"] == 42
        assert r.headers["retry-after"] == "42"

    @patch("app.routes.support.send_support_ticket_email", new_callable=AsyncMock)
    @patch("app.routes.support.SupabaseStorage")
    @patch("app.routes.support.mark_email_sent", new_callable=AsyncMock)
    @patch("app.routes.support.create_ticket", new_callable=AsyncMock)
    @patch("app.routes.support.get_session_factory")
    @patch("app.routes.support.get_settings")
    @patch("app.routes.support.enforce_support_submission", new_callable=AsyncMock)
    def test_allowed_passes_through(
        self, mock_rl, mock_settings, mock_factory, mock_create,
        mock_mark, mock_storage_cls, mock_send,
    ):
        from app.utils.rate_limit import RateLimitDecision
        mock_rl.return_value = RateLimitDecision(allowed=True)
        mock_settings.return_value = MagicMock(
            support_max_attachments=3, support_max_attachment_bytes=5_242_880,
            support_attachment_ttl_seconds=3600,
            support_rate_limit_per_hour=5, support_rate_limit_per_day=20,
            supabase_url="x", supabase_service_role_key="y",
            signed_url_ttl_seconds=60,
        )
        ticket = MagicMock()
        ticket.id = uuid.UUID(int=42)
        from datetime import datetime, timezone
        ticket.created_at = datetime(2026, 5, 11, tzinfo=timezone.utc)
        mock_create.return_value = ticket
        mock_send.return_value = "msg-id"
        factory, _ = _session_factory_mock()
        mock_factory.return_value = factory

        app = _build_support_app(_user())
        with TestClient(app) as client:
            r = client.post(
                "/support/tickets",
                data={"category": "bug", "subject": "x", "message": "x"},
            )
        assert r.status_code == 200
        # rate-limit dep was called with the configured values
        kwargs = mock_rl.call_args.kwargs
        assert kwargs["per_hour"] == 5
        assert kwargs["per_day"] == 20


# ---------------------------------------------------------------------------
# Rate-limit primitive: check_and_increment + enforce_support_submission
# (exercises the Redis pipeline interaction with a mocked client).
# ---------------------------------------------------------------------------


class TestRateLimitPrimitive:
    @pytest.mark.asyncio
    async def test_under_limit_allowed(self):
        from app.utils import rate_limit
        client = MagicMock()
        pipe = MagicMock()
        pipe.incr = MagicMock()
        pipe.ttl = MagicMock()
        pipe.execute = AsyncMock(return_value=[3, 100])
        client.pipeline = MagicMock(return_value=pipe)
        client.expire = AsyncMock()
        with patch.object(rate_limit, "_client", lambda: client):
            decision = await rate_limit.check_and_increment(
                key="rl:test:1", limit=5, window_seconds=60,
            )
        assert decision.allowed is True

    @pytest.mark.asyncio
    async def test_over_limit_blocked_and_returns_ttl(self):
        from app.utils import rate_limit
        client = MagicMock()
        pipe = MagicMock()
        pipe.incr = MagicMock()
        pipe.ttl = MagicMock()
        pipe.execute = AsyncMock(return_value=[6, 17])
        client.pipeline = MagicMock(return_value=pipe)
        client.expire = AsyncMock()
        with patch.object(rate_limit, "_client", lambda: client):
            decision = await rate_limit.check_and_increment(
                key="rl:test:2", limit=5, window_seconds=60,
            )
        assert decision.allowed is False
        assert decision.retry_after_seconds == 17

    @pytest.mark.asyncio
    async def test_negative_ttl_sets_expire(self):
        from app.utils import rate_limit
        client = MagicMock()
        pipe = MagicMock()
        pipe.incr = MagicMock()
        pipe.ttl = MagicMock()
        pipe.execute = AsyncMock(return_value=[1, -1])
        client.pipeline = MagicMock(return_value=pipe)
        client.expire = AsyncMock()
        with patch.object(rate_limit, "_client", lambda: client):
            decision = await rate_limit.check_and_increment(
                key="rl:test:3", limit=5, window_seconds=60,
            )
        assert decision.allowed is True
        client.expire.assert_awaited_once_with("rl:test:3", 60)

    @pytest.mark.asyncio
    async def test_redis_failure_fails_open(self):
        from app.utils import rate_limit
        client = MagicMock()
        client.pipeline = MagicMock(side_effect=RuntimeError("boom"))
        with patch.object(rate_limit, "_client", lambda: client):
            decision = await rate_limit.check_and_increment(
                key="rl:test:4", limit=5, window_seconds=60,
            )
        assert decision.allowed is True

    @pytest.mark.asyncio
    async def test_zero_limit_means_disabled(self):
        from app.utils import rate_limit
        decision = await rate_limit.check_and_increment(
            key="rl:test:5", limit=0, window_seconds=60,
        )
        assert decision.allowed is True

    @pytest.mark.asyncio
    async def test_enforce_checks_hour_then_day(self):
        from app.utils import rate_limit
        calls: list[tuple] = []

        async def fake(*, key, limit, window_seconds):
            calls.append((key, limit, window_seconds))
            return rate_limit.RateLimitDecision(allowed=True)

        with patch.object(rate_limit, "check_and_increment", fake):
            decision = await rate_limit.enforce_support_submission(
                user_id="u1", per_hour=5, per_day=20,
            )
        assert decision.allowed is True
        assert calls[0][0] == "rl:support:hour:u1"
        assert calls[0][2] == 3600
        assert calls[1][0] == "rl:support:day:u1"
        assert calls[1][2] == 86400

    @pytest.mark.asyncio
    async def test_enforce_short_circuits_on_hour_block(self):
        from app.utils import rate_limit
        calls: list[tuple] = []

        async def fake(*, key, limit, window_seconds):
            calls.append((key,))
            return rate_limit.RateLimitDecision(allowed=False, retry_after_seconds=99)

        with patch.object(rate_limit, "check_and_increment", fake):
            decision = await rate_limit.enforce_support_submission(
                user_id="u1", per_hour=5, per_day=20,
            )
        assert decision.allowed is False
        assert decision.retry_after_seconds == 99
        assert len(calls) == 1  # day bucket NOT consulted
