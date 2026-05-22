"""Slice 17: production Dockerfile + VM deployment."""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]


# ── Dockerfile ────────────────────────────────────────────────────────────────


def test_dockerfile_exists() -> None:
    assert (REPO_ROOT / "Dockerfile").exists()


def test_dockerfile_spa_build_stage() -> None:
    content = (REPO_ROOT / "Dockerfile").read_text()
    assert "FROM node" in content
    assert "npm" in content


def test_dockerfile_python_runtime_stage() -> None:
    content = (REPO_ROOT / "Dockerfile").read_text()
    assert "FROM python:3.11" in content


def test_dockerfile_exposes_8080() -> None:
    content = (REPO_ROOT / "Dockerfile").read_text()
    assert "8080" in content
    assert "EXPOSE" in content


def test_dockerfile_copies_spa_dist() -> None:
    content = (REPO_ROOT / "Dockerfile").read_text()
    assert "dist" in content


# ── scripts/run.sh ────────────────────────────────────────────────────────────


def test_run_sh_exists() -> None:
    assert (REPO_ROOT / "scripts" / "run.sh").exists()


def test_run_sh_is_executable() -> None:
    run_sh = REPO_ROOT / "scripts" / "run.sh"
    assert run_sh.stat().st_mode & stat.S_IXUSR


def test_run_sh_port_detection() -> None:
    content = (REPO_ROOT / "scripts" / "run.sh").read_text()
    assert "nc -z" in content
    assert "8080" in content


def test_run_sh_docker_run() -> None:
    content = (REPO_ROOT / "scripts" / "run.sh").read_text()
    assert "docker run" in content


def test_run_sh_prints_access_url() -> None:
    content = (REPO_ROOT / "scripts" / "run.sh").read_text()
    assert "http://" in content


def test_run_sh_port_fallback_loop() -> None:
    content = (REPO_ROOT / "scripts" / "run.sh").read_text()
    assert "8081" in content or "port + 1" in content or "port=$" in content


# ── API startup logging ───────────────────────────────────────────────────────


def test_startup_logs_custom_port(caplog: pytest.LogCaptureFixture) -> None:
    """Startup lifespan logs the PORT env variable."""
    app = create_app()
    with patch.dict(os.environ, {"PORT": "9999"}):
        with caplog.at_level(logging.INFO, logger="evidence_factory.api"):
            with TestClient(app):
                pass
    assert "9999" in caplog.text


def test_startup_logs_default_port(caplog: pytest.LogCaptureFixture) -> None:
    """Startup lifespan defaults to 8080 when PORT is not set."""
    app = create_app()
    saved = os.environ.pop("PORT", None)
    try:
        with caplog.at_level(logging.INFO, logger="evidence_factory.api"):
            with TestClient(app):
                pass
    finally:
        if saved is not None:
            os.environ["PORT"] = saved
    assert "8080" in caplog.text
