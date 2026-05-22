"""Slice 19: clear startup warning when ui-app/dist/ is absent."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import api.main as api_main
from api.main import create_app


def test_missing_dist_logs_warning(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """When ui-app/dist/ is absent, startup logs an actionable build instruction."""
    absent = tmp_path / "nonexistent"
    with patch.object(api_main, "UI_DIST", absent):
        app = create_app()
        with caplog.at_level(logging.WARNING, logger="evidence_factory.api"):
            with TestClient(app):
                pass
    assert "npm run build" in caplog.text


def test_present_dist_no_missing_warning(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """When ui-app/dist/ exists with index.html, no missing-dist warning is emitted."""
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<html></html>")
    with patch.object(api_main, "UI_DIST", dist):
        app = create_app()
        with caplog.at_level(logging.WARNING, logger="evidence_factory.api"):
            with TestClient(app):
                pass
    assert "npm run build" not in caplog.text
