"""Tests for L4 artifact_class + confidentiality validation in the ingest route."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.routes.ingest import _validate_artifact_class, _validate_confidentiality


class TestArtifactClassValidation:
    def test_default_when_none(self):
        assert _validate_artifact_class(None) == "other"

    def test_default_when_blank(self):
        assert _validate_artifact_class("   ") == "other"

    def test_lowercases(self):
        assert _validate_artifact_class("DDR") == "ddr"

    def test_accepts_known_class(self):
        for cls in ("master", "program", "ddr", "handover", "service",
                    "risk", "action", "learning", "media", "offset", "other"):
            assert _validate_artifact_class(cls) == cls

    def test_rejects_unknown(self):
        with pytest.raises(HTTPException) as exc:
            _validate_artifact_class("invoice")
        assert exc.value.status_code == 400
        assert "invalid artifact_class" in exc.value.detail


class TestConfidentialityValidation:
    def test_default_when_none(self):
        assert _validate_confidentiality(None) == "internal"

    def test_lowercases(self):
        assert _validate_confidentiality("Restricted") == "restricted"

    def test_accepts_known(self):
        for c in ("public", "internal", "restricted", "third_party"):
            assert _validate_confidentiality(c) == c

    def test_rejects_unknown(self):
        with pytest.raises(HTTPException) as exc:
            _validate_confidentiality("top_secret")
        assert exc.value.status_code == 400
        assert "invalid confidentiality" in exc.value.detail
