"""Slice 1 tests for in-app support tickets.

These exercise the route layer end-to-end against a FastAPI app, with the
DB / Supabase Storage / Resend boundaries mocked. Pure unit-test style
to match the rest of the suite (no live services).
"""

from __future__ import annotations

import io
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.deps import current_user
from app.auth.middleware import AuthUser
from app.routes.support import _safe_filename, router as support_router


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

USER_ID = str(uuid.uuid4())
OTHER_USER_ID = str(uuid.uuid4())


@pytest.fixture(autouse=True)
def _bypass_rate_limit():
    """Slice 2 added a rate-limit gate on POST /support/tickets — bypass it
    for the Slice 1 happy-path / validation tests so they keep asserting
    the original behaviours."""
    from app.utils.rate_limit import RateLimitDecision
    with patch(
        "app.routes.support.enforce_support_submission",
        new_callable=AsyncMock,
    ) as m:
        m.return_value = RateLimitDecision(allowed=True)
        yield m


def _make_user(role: str = "drilling_engineer") -> AuthUser:
    return AuthUser(
        id=USER_ID,
        email="user@example.com",
        role=role,
        display_name="Test User",
        organization="Test Org",
    )


def _build_app(user: AuthUser) -> FastAPI:
    app = FastAPI()
    app.include_router(support_router)
    app.dependency_overrides[current_user] = lambda: user
    return app


@pytest.fixture
def fake_session_factory():
    """Yields an AsyncSession-like object usable as `async with Session() as s`."""
    sess = MagicMock()
    sess.commit = AsyncMock()
    sess.flush = AsyncMock()
    sess.execute = AsyncMock()
    sess.get = AsyncMock(return_value=None)
    sess.add = MagicMock()

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=sess)
    cm.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=cm)
    return factory, sess


# ---------------------------------------------------------------------------
# _safe_filename
# ---------------------------------------------------------------------------


class TestSafeFilename:
    def test_strips_path_components(self):
        assert _safe_filename("../../etc/passwd") == "passwd"

    def test_collapses_unsafe_characters(self):
        assert _safe_filename("my file (1).png") == "my_file_1_.png"

    def test_handles_windows_separators(self):
        assert _safe_filename("C:\\Users\\Foo\\report.pdf") == "report.pdf"

    def test_falls_back_when_only_unsafe(self):
        assert _safe_filename("@@@") == "attachment"

    def test_truncates_to_120_chars(self):
        out = _safe_filename("a" * 500 + ".png")
        assert len(out) == 120


# ---------------------------------------------------------------------------
# POST /support/tickets — validation
# ---------------------------------------------------------------------------


class TestSubmitValidation:
    def test_invalid_category_returns_400(self):
        app = _build_app(_make_user())
        with TestClient(app) as client:
            r = client.post(
                "/support/tickets",
                data={
                    "category": "rant",
                    "subject": "x",
                    "message": "x",
                },
            )
        assert r.status_code == 400
        body = r.json()["detail"]
        assert body["error_code"] == "invalid_category"
        assert "bug" in body["allowed"]

    def test_missing_subject_returns_422(self):
        app = _build_app(_make_user())
        with TestClient(app) as client:
            r = client.post(
                "/support/tickets",
                data={"category": "bug", "message": "x"},
            )
        # FastAPI Form(...) missing -> 422 validation error
        assert r.status_code == 422

    def test_subject_too_long_returns_422(self):
        app = _build_app(_make_user())
        with TestClient(app) as client:
            r = client.post(
                "/support/tickets",
                data={
                    "category": "bug",
                    "subject": "a" * 121,
                    "message": "x",
                },
            )
        assert r.status_code == 422

    def test_message_too_long_returns_422(self):
        app = _build_app(_make_user())
        with TestClient(app) as client:
            r = client.post(
                "/support/tickets",
                data={
                    "category": "bug",
                    "subject": "x",
                    "message": "m" * 4001,
                },
            )
        assert r.status_code == 422

    def test_diagnostics_required_when_flag_set(self):
        app = _build_app(_make_user())
        with TestClient(app) as client:
            r = client.post(
                "/support/tickets",
                data={
                    "category": "bug",
                    "subject": "x",
                    "message": "x",
                    "include_diagnostics": "true",
                },
            )
        assert r.status_code == 400
        assert r.json()["detail"] == "diagnostics_required_when_included"

    def test_diagnostics_must_be_valid_json(self):
        app = _build_app(_make_user())
        with TestClient(app) as client:
            r = client.post(
                "/support/tickets",
                data={
                    "category": "bug",
                    "subject": "x",
                    "message": "x",
                    "include_diagnostics": "true",
                    "diagnostics": "not json",
                },
            )
        assert r.status_code == 400
        assert "diagnostics_not_json" in r.json()["detail"]

    def test_diagnostics_must_be_object(self):
        app = _build_app(_make_user())
        with TestClient(app) as client:
            r = client.post(
                "/support/tickets",
                data={
                    "category": "bug",
                    "subject": "x",
                    "message": "x",
                    "include_diagnostics": "true",
                    "diagnostics": "[1, 2, 3]",
                },
            )
        assert r.status_code == 400
        assert r.json()["detail"] == "diagnostics_must_be_object"


class TestAttachmentValidation:
    @patch("app.routes.support.get_settings")
    def test_too_many_attachments_returns_400(self, mock_settings):
        mock_settings.return_value = MagicMock(
            support_max_attachments=3, support_max_attachment_bytes=5_242_880
        )
        app = _build_app(_make_user())
        files = [
            ("attachments", (f"f{i}.png", io.BytesIO(b"x"), "image/png"))
            for i in range(4)
        ]
        with TestClient(app) as client:
            r = client.post(
                "/support/tickets",
                data={"category": "bug", "subject": "x", "message": "x"},
                files=files,
            )
        assert r.status_code == 400
        assert r.json()["detail"]["error_code"] == "too_many_attachments"

    @patch("app.routes.support.get_settings")
    def test_unsupported_mime_returns_400(self, mock_settings):
        mock_settings.return_value = MagicMock(
            support_max_attachments=3, support_max_attachment_bytes=5_242_880
        )
        app = _build_app(_make_user())
        with TestClient(app) as client:
            r = client.post(
                "/support/tickets",
                data={"category": "bug", "subject": "x", "message": "x"},
                files=[("attachments", ("x.exe", io.BytesIO(b"x"), "application/x-msdownload"))],
            )
        assert r.status_code == 400
        assert r.json()["detail"]["error_code"] == "unsupported_attachment_type"

    @patch("app.routes.support.get_settings")
    def test_attachment_too_large_returns_413(self, mock_settings):
        mock_settings.return_value = MagicMock(
            support_max_attachments=3,
            support_max_attachment_bytes=10,  # tiny cap to trigger
        )
        app = _build_app(_make_user())
        with TestClient(app) as client:
            r = client.post(
                "/support/tickets",
                data={"category": "bug", "subject": "x", "message": "x"},
                files=[("attachments", ("big.png", io.BytesIO(b"x" * 100), "image/png"))],
            )
        assert r.status_code == 413
        assert r.json()["detail"]["error_code"] == "attachment_too_large"


# ---------------------------------------------------------------------------
# POST /support/tickets — happy path (mocked DB + storage + email)
# ---------------------------------------------------------------------------


class TestSubmitHappyPath:
    def _mock_settings(self):
        return MagicMock(
            support_max_attachments=3,
            support_max_attachment_bytes=5_242_880,
            support_attachment_ttl_seconds=604_800,
            supabase_url="x",
            supabase_service_role_key="y",
            signed_url_ttl_seconds=60,
        )

    def _mock_ticket(self):
        t = MagicMock()
        t.id = uuid.UUID(int=1)
        t.created_at = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)
        return t

    @patch("app.routes.support.send_support_ticket_email", new_callable=AsyncMock)
    @patch("app.routes.support.SupabaseStorage")
    @patch("app.routes.support.mark_email_sent", new_callable=AsyncMock)
    @patch("app.routes.support.add_attachment", new_callable=AsyncMock)
    @patch("app.routes.support.create_ticket", new_callable=AsyncMock)
    @patch("app.routes.support.get_session_factory")
    @patch("app.routes.support.get_settings")
    def test_minimal_submit_returns_200(
        self, mock_settings, mock_factory, mock_create,
        mock_add_att, mock_mark_email, mock_storage_cls, mock_send_email,
    ):
        mock_settings.return_value = self._mock_settings()
        ticket = self._mock_ticket()
        mock_create.return_value = ticket
        mock_send_email.return_value = "msg-id-123"

        sess = MagicMock()
        sess.commit = AsyncMock()
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=sess)
        cm.__aexit__ = AsyncMock(return_value=None)
        mock_factory.return_value = MagicMock(return_value=cm)

        app = _build_app(_make_user())
        with TestClient(app) as client:
            r = client.post(
                "/support/tickets",
                data={
                    "category": "bug",
                    "subject": "Help",
                    "message": "Something is broken",
                },
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ticket_id"] == str(ticket.id)
        assert body["status"] == "open"
        assert body["email_sent"] is True
        assert body["created_at"].startswith("2026-05-11")

        # create_ticket called with normalized values
        kwargs = mock_create.call_args.kwargs
        assert kwargs["user_id"] == USER_ID
        assert kwargs["category"] == "bug"
        assert kwargs["subject"] == "Help"
        assert kwargs["message"] == "Something is broken"
        assert kwargs["diagnostics"] is None

        # email composition got the right ticket_id
        send_kwargs = mock_send_email.call_args.kwargs
        assert send_kwargs["ticket_id"] == str(ticket.id)
        assert send_kwargs["category"] == "bug"
        assert send_kwargs["user_email"] == "user@example.com"
        assert send_kwargs["attachment_signed_urls"] == []

        # mark_email_sent called with the upstream message id
        mock_mark_email.assert_awaited_once()
        assert mock_mark_email.call_args.kwargs["message_id"] == "msg-id-123"

    @patch("app.routes.support.send_support_ticket_email", new_callable=AsyncMock)
    @patch("app.routes.support.SupabaseStorage")
    @patch("app.routes.support.mark_email_sent", new_callable=AsyncMock)
    @patch("app.routes.support.add_attachment", new_callable=AsyncMock)
    @patch("app.routes.support.create_ticket", new_callable=AsyncMock)
    @patch("app.routes.support.get_session_factory")
    @patch("app.routes.support.get_settings")
    def test_submit_with_attachments_uploads_each(
        self, mock_settings, mock_factory, mock_create,
        mock_add_att, mock_mark_email, mock_storage_cls, mock_send_email,
    ):
        mock_settings.return_value = self._mock_settings()
        mock_create.return_value = self._mock_ticket()
        mock_send_email.return_value = None

        storage_inst = MagicMock()
        storage_inst.write_support_attachment = AsyncMock(
            side_effect=lambda **kw: f"key/{kw['filename']}"
        )
        storage_inst.signed_url_support_attachment = MagicMock(
            return_value="https://signed/example"
        )
        mock_storage_cls.return_value = storage_inst

        sess = MagicMock()
        sess.commit = AsyncMock()
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=sess)
        cm.__aexit__ = AsyncMock(return_value=None)
        mock_factory.return_value = MagicMock(return_value=cm)

        app = _build_app(_make_user())
        with TestClient(app) as client:
            r = client.post(
                "/support/tickets",
                data={"category": "bug", "subject": "x", "message": "x"},
                files=[
                    ("attachments", ("a.png", io.BytesIO(b"PNG_BYTES"), "image/png")),
                    ("attachments", ("b.pdf", io.BytesIO(b"PDF_BYTES"), "application/pdf")),
                ],
            )
        assert r.status_code == 200, r.text

        # Two writes + two DB inserts
        assert storage_inst.write_support_attachment.await_count == 2
        assert mock_add_att.await_count == 2
        # Email got two signed URLs
        send_kwargs = mock_send_email.call_args.kwargs
        assert len(send_kwargs["attachment_signed_urls"]) == 2
        assert all(a["url"] == "https://signed/example" for a in send_kwargs["attachment_signed_urls"])
        assert {a["filename"] for a in send_kwargs["attachment_signed_urls"]} == {"a.png", "b.pdf"}

    @patch("app.routes.support.send_support_ticket_email", new_callable=AsyncMock)
    @patch("app.routes.support.SupabaseStorage")
    @patch("app.routes.support.add_attachment", new_callable=AsyncMock)
    @patch("app.routes.support.create_ticket", new_callable=AsyncMock)
    @patch("app.routes.support.get_session_factory")
    @patch("app.routes.support.get_settings")
    def test_email_failure_returns_502_but_ticket_persists(
        self, mock_settings, mock_factory, mock_create,
        mock_add_att, mock_storage_cls, mock_send_email,
    ):
        mock_settings.return_value = self._mock_settings()
        mock_create.return_value = self._mock_ticket()
        mock_send_email.side_effect = RuntimeError("resend down")

        sess = MagicMock()
        sess.commit = AsyncMock()
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=sess)
        cm.__aexit__ = AsyncMock(return_value=None)
        mock_factory.return_value = MagicMock(return_value=cm)

        app = _build_app(_make_user())
        with TestClient(app) as client:
            r = client.post(
                "/support/tickets",
                data={"category": "bug", "subject": "x", "message": "x"},
            )
        assert r.status_code == 502
        body = r.json()["detail"]
        assert body["error_code"] == "email_upstream_failed"
        assert body["ticket_id"] == str(self._mock_ticket().id)
        # ticket commit happened before email attempt
        assert mock_create.await_count == 1


# ---------------------------------------------------------------------------
# GET /support/tickets/me  (list)
# ---------------------------------------------------------------------------


class TestListMyTickets:
    @patch("app.routes.support.list_my_tickets", new_callable=AsyncMock)
    @patch("app.routes.support.get_session_factory")
    def test_returns_items_and_cursor(self, mock_factory, mock_list):
        mock_list.return_value = (
            [
                {
                    "ticket_id": "t1",
                    "category": "bug",
                    "subject": "S1",
                    "status": "open",
                    "created_at": "2026-05-11T10:00:00+00:00",
                    "updated_at": "2026-05-11T10:00:00+00:00",
                    "last_admin_note": None,
                    "attachment_count": 0,
                }
            ],
            "next-cursor-xyz",
        )
        sess = MagicMock()
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=sess)
        cm.__aexit__ = AsyncMock(return_value=None)
        mock_factory.return_value = MagicMock(return_value=cm)

        app = _build_app(_make_user())
        with TestClient(app) as client:
            r = client.get("/support/tickets/me?limit=10")
        assert r.status_code == 200
        body = r.json()
        assert body["next_cursor"] == "next-cursor-xyz"
        assert len(body["items"]) == 1
        assert body["items"][0]["ticket_id"] == "t1"

        # query helper got the right arguments
        kwargs = mock_list.call_args.kwargs
        assert kwargs["user_id"] == USER_ID
        assert kwargs["limit"] == 10
        assert kwargs["cursor"] is None
        assert kwargs["status"] is None

    def test_invalid_status_returns_400(self):
        app = _build_app(_make_user())
        with TestClient(app) as client:
            r = client.get("/support/tickets/me?status=bogus")
        assert r.status_code == 400
        assert r.json()["detail"]["error_code"] == "invalid_status"

    def test_limit_clamped_high_returns_422(self):
        app = _build_app(_make_user())
        with TestClient(app) as client:
            r = client.get("/support/tickets/me?limit=9999")
        # ge=1, le=100 -> FastAPI validation returns 422
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# GET /support/tickets/me/{ticket_id}  (detail)
# ---------------------------------------------------------------------------


class TestGetMyTicket:
    @patch("app.routes.support.get_my_ticket", new_callable=AsyncMock)
    @patch("app.routes.support.get_session_factory")
    def test_returns_full_detail(self, mock_factory, mock_get):
        mock_get.return_value = {
            "ticket_id": "abc",
            "category": "bug",
            "subject": "S",
            "message": "M",
            "status": "open",
            "diagnostics": {"ua": "x"},
            "attachments": [],
            "notes": [],
            "created_at": "2026-05-11T10:00:00+00:00",
            "updated_at": "2026-05-11T10:00:00+00:00",
        }
        sess = MagicMock()
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=sess)
        cm.__aexit__ = AsyncMock(return_value=None)
        mock_factory.return_value = MagicMock(return_value=cm)

        app = _build_app(_make_user())
        with TestClient(app) as client:
            r = client.get("/support/tickets/me/abc")
        assert r.status_code == 200
        body = r.json()
        assert body["ticket_id"] == "abc"
        assert body["diagnostics"] == {"ua": "x"}

    @patch("app.routes.support.get_my_ticket", new_callable=AsyncMock)
    @patch("app.routes.support.get_session_factory")
    def test_unknown_returns_404(self, mock_factory, mock_get):
        mock_get.return_value = None
        sess = MagicMock()
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=sess)
        cm.__aexit__ = AsyncMock(return_value=None)
        mock_factory.return_value = MagicMock(return_value=cm)

        app = _build_app(_make_user())
        with TestClient(app) as client:
            r = client.get("/support/tickets/me/does-not-exist")
        assert r.status_code == 404
        assert r.json()["detail"] == "ticket_not_found"


# ---------------------------------------------------------------------------
# GET /support/tickets/{id}/attachments/{aid}
# ---------------------------------------------------------------------------


class TestFetchAttachment:
    @patch("app.routes.support.SupabaseStorage")
    @patch("app.routes.support.get_attachment_for_user", new_callable=AsyncMock)
    @patch("app.routes.support.get_session_factory")
    @patch("app.routes.support.get_settings")
    def test_owner_gets_302_signed_url(
        self, mock_settings, mock_factory, mock_get_att, mock_storage_cls,
    ):
        mock_settings.return_value = MagicMock(
            supabase_url="x", supabase_service_role_key="y",
            signed_url_ttl_seconds=60,
        )
        att = MagicMock()
        att.storage_key = "u/support/t/x.png"
        mock_get_att.return_value = att

        storage_inst = MagicMock()
        storage_inst.signed_url_support_attachment = MagicMock(
            return_value="https://signed.example/x"
        )
        mock_storage_cls.return_value = storage_inst

        sess = MagicMock()
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=sess)
        cm.__aexit__ = AsyncMock(return_value=None)
        mock_factory.return_value = MagicMock(return_value=cm)

        app = _build_app(_make_user())
        with TestClient(app, follow_redirects=False) as client:
            r = client.get("/support/tickets/t1/attachments/a1")
        assert r.status_code == 302
        assert r.headers["location"] == "https://signed.example/x"

        # is_super_admin=False for normal user
        assert mock_get_att.call_args.kwargs["is_super_admin"] is False

    @patch("app.routes.support.get_attachment_for_user", new_callable=AsyncMock)
    @patch("app.routes.support.get_session_factory")
    def test_unknown_returns_404(self, mock_factory, mock_get_att):
        mock_get_att.return_value = None
        sess = MagicMock()
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=sess)
        cm.__aexit__ = AsyncMock(return_value=None)
        mock_factory.return_value = MagicMock(return_value=cm)

        app = _build_app(_make_user())
        with TestClient(app) as client:
            r = client.get("/support/tickets/t1/attachments/a1")
        assert r.status_code == 404

    @patch("app.routes.support.SupabaseStorage")
    @patch("app.routes.support.get_attachment_for_user", new_callable=AsyncMock)
    @patch("app.routes.support.get_session_factory")
    @patch("app.routes.support.get_settings")
    def test_super_admin_flag_propagates(
        self, mock_settings, mock_factory, mock_get_att, mock_storage_cls,
    ):
        mock_settings.return_value = MagicMock(
            supabase_url="x", supabase_service_role_key="y",
            signed_url_ttl_seconds=60,
        )
        att = MagicMock()
        att.storage_key = "u/support/t/x.png"
        mock_get_att.return_value = att
        mock_storage_cls.return_value.signed_url_support_attachment = MagicMock(
            return_value="https://signed/x"
        )

        sess = MagicMock()
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=sess)
        cm.__aexit__ = AsyncMock(return_value=None)
        mock_factory.return_value = MagicMock(return_value=cm)

        app = _build_app(_make_user(role="super_admin"))
        with TestClient(app, follow_redirects=False) as client:
            r = client.get("/support/tickets/t1/attachments/a1")
        assert r.status_code == 302
        assert mock_get_att.call_args.kwargs["is_super_admin"] is True


# ---------------------------------------------------------------------------
# /auth/me — is_superadmin derived flag (covered indirectly here so a
# regression on the FE contract is caught at BE test time).
# ---------------------------------------------------------------------------


class TestAuthMeIsSuperadmin:
    def test_serialize_emits_is_superadmin_true_for_super_admin(self):
        from app.auth.routes import _serialize

        out = _serialize(_make_user(role="super_admin"))
        assert out["is_superadmin"] is True
        assert out["role"] == "super_admin"

    def test_serialize_emits_is_superadmin_false_for_regular_user(self):
        from app.auth.routes import _serialize

        out = _serialize(_make_user(role="drilling_engineer"))
        assert out["is_superadmin"] is False
