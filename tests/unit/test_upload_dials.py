"""Slice 14: Upload endpoint must forward difficulty dials to the pipeline.

Tests that POST /api/runs/upload accepts preset + dial Form fields and that
_settings_from_dials() builds RunSettings correctly from those values.
"""

from __future__ import annotations

import io
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.main import _settings_from_dials, app
from api.pipeline.orchestrator import RunSettings, settings_for_preset

# ---------------------------------------------------------------------------
# _settings_from_dials unit tests
# ---------------------------------------------------------------------------


def test_settings_from_dials_default_is_medium() -> None:
    s = _settings_from_dials()
    medium = settings_for_preset("medium")
    assert s.owners_per_proposition == medium.owners_per_proposition
    assert s.noise_count == medium.noise_count


def test_settings_from_dials_easy_preset() -> None:
    s = _settings_from_dials(difficulty="easy")
    easy = settings_for_preset("easy")
    assert s.owners_per_proposition == easy.owners_per_proposition
    assert s.target_artifact_count == easy.target_artifact_count


def test_settings_from_dials_hard_preset() -> None:
    s = _settings_from_dials(difficulty="hard")
    hard = settings_for_preset("hard")
    assert s.noise_count == hard.noise_count
    assert s.red_herring_count == hard.red_herring_count


def test_settings_from_dials_per_dial_override_wins() -> None:
    s = _settings_from_dials(difficulty="easy", owners_per_proposition=9, noise_count=42)
    assert s.owners_per_proposition == 9
    assert s.noise_count == 42
    # Non-overridden fields still reflect the preset.
    easy = settings_for_preset("easy")
    assert s.fragmentation_factor == easy.fragmentation_factor


def test_settings_from_dials_returns_runsettings_instance() -> None:
    assert isinstance(_settings_from_dials(), RunSettings)


# ---------------------------------------------------------------------------
# Upload endpoint integration tests (mocked pipeline)
# ---------------------------------------------------------------------------


def _file_bytes() -> bytes:
    return b"The quick brown fox jumped over the lazy dog."


def _make_multipart(
    content: bytes = _file_bytes(),
    filename: str = "story.txt",
    **extra_fields: str,
) -> dict:
    """Return files + data dicts for TestClient multipart post."""
    files = {"file": (filename, io.BytesIO(content), "text/plain")}
    data = {"attestation_checked": "true", **extra_fields}
    return {"files": files, "data": data}


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_upload_without_dials_uses_medium_defaults(client: TestClient) -> None:
    """Upload with no dial fields defaults to medium preset settings."""
    captured: list[RunSettings] = []

    def fake_make_drive(state, source_text, attestation_checked, settings=None):  # type: ignore[override]
        from api.pipeline.orchestrator import RunSettings
        captured.append(settings or RunSettings())

    with patch("api.main._make_drive", side_effect=fake_make_drive):
        resp = client.post("/api/runs/upload", **_make_multipart())

    assert resp.status_code == 200
    assert len(captured) == 1
    medium = settings_for_preset("medium")
    assert captured[0].owners_per_proposition == medium.owners_per_proposition
    assert captured[0].noise_count == medium.noise_count


def test_upload_easy_preset_forwarded_to_pipeline(client: TestClient) -> None:
    """Upload with difficulty=easy must forward easy preset settings."""
    captured: list[RunSettings] = []

    def fake_make_drive(state, source_text, attestation_checked, settings=None):  # type: ignore[override]
        from api.pipeline.orchestrator import RunSettings
        captured.append(settings or RunSettings())

    params = _make_multipart(difficulty="easy")
    with patch("api.main._make_drive", side_effect=fake_make_drive):
        resp = client.post("/api/runs/upload", **params)

    assert resp.status_code == 200
    easy = settings_for_preset("easy")
    assert captured[0].owners_per_proposition == easy.owners_per_proposition
    assert captured[0].target_artifact_count == easy.target_artifact_count
    assert captured[0].noise_count == easy.noise_count


def test_upload_hard_preset_forwarded_to_pipeline(client: TestClient) -> None:
    """Upload with difficulty=hard must forward hard preset settings."""
    captured: list[RunSettings] = []

    def fake_make_drive(state, source_text, attestation_checked, settings=None):  # type: ignore[override]
        from api.pipeline.orchestrator import RunSettings
        captured.append(settings or RunSettings())

    params = _make_multipart(difficulty="hard")
    with patch("api.main._make_drive", side_effect=fake_make_drive):
        resp = client.post("/api/runs/upload", **params)

    assert resp.status_code == 200
    hard = settings_for_preset("hard")
    assert captured[0].owners_per_proposition == hard.owners_per_proposition
    assert captured[0].noise_count == hard.noise_count


def test_upload_per_dial_overrides_forwarded(client: TestClient) -> None:
    """Upload with per-dial form fields must override the preset."""
    captured: list[RunSettings] = []

    def fake_make_drive(state, source_text, attestation_checked, settings=None):  # type: ignore[override]
        from api.pipeline.orchestrator import RunSettings
        captured.append(settings or RunSettings())

    params = _make_multipart(
        difficulty="medium",
        owners_per_proposition="7",
        noise_count="123",
        dominance_margin="0.22",
    )
    with patch("api.main._make_drive", side_effect=fake_make_drive):
        resp = client.post("/api/runs/upload", **params)

    assert resp.status_code == 200
    assert captured[0].owners_per_proposition == 7
    assert captured[0].noise_count == 123
    assert abs(captured[0].dominance_margin - 0.22) < 1e-9


def test_upload_dials_differ_from_defaults_when_preset_changed(client: TestClient) -> None:
    """Uploading with easy vs hard preset produces different RunSettings."""
    easy_settings: list[RunSettings] = []
    hard_settings: list[RunSettings] = []

    def capture_easy(state, source_text, attestation_checked, settings=None):  # type: ignore[override]
        from api.pipeline.orchestrator import RunSettings
        easy_settings.append(settings or RunSettings())

    def capture_hard(state, source_text, attestation_checked, settings=None):  # type: ignore[override]
        from api.pipeline.orchestrator import RunSettings
        hard_settings.append(settings or RunSettings())

    with patch("api.main._make_drive", side_effect=capture_easy):
        client.post("/api/runs/upload", **_make_multipart(difficulty="easy"))

    with patch("api.main._make_drive", side_effect=capture_hard):
        client.post("/api/runs/upload", **_make_multipart(difficulty="hard"))

    assert easy_settings[0].noise_count < hard_settings[0].noise_count
    assert easy_settings[0].owners_per_proposition < hard_settings[0].owners_per_proposition
