"""Unit tests for the cast pipeline stage wired into the orchestrator."""

from __future__ import annotations

import csv
import io
import zipfile

from api.pipeline.orchestrator import run_sync

_SHORT_SOURCE = "Alice and Bob worked at the firm. Carol supervised them from afar."


def _run(source: str = _SHORT_SOURCE) -> tuple[list, object]:
    return run_sync(source, attestation_checked=True)


def test_cast_stage_emits_started_event():
    events, _ = _run()
    assert ("cast", "started") in [(e.stage, e.status) for e in events]


def test_cast_stage_emits_complete_event():
    events, _ = _run()
    complete = [e for e in events if e.stage == "cast" and e.status == "complete"]
    assert len(complete) == 1


def test_cast_complete_event_includes_persona_count():
    events, _ = _run()
    cast_complete = next(e for e in events if e.stage == "cast" and e.status == "complete")
    assert "personas" in cast_complete.detail
    assert cast_complete.detail["personas"] >= 2


def test_cast_stage_is_between_extract_and_events():
    events, _ = _run()
    stage_names = [e.stage for e in events]
    extract_idx = stage_names.index("extract")
    cast_idx = stage_names.index("cast")
    events_idx = stage_names.index("events")
    assert extract_idx < cast_idx < events_idx


def test_default_registry_personas_not_in_manifest():
    """Pipeline no longer uses the hardcoded Holmes cast; manifest should have fixture personas."""
    events, result = _run()
    assert result is not None
    with zipfile.ZipFile(io.BytesIO(result.zip_bytes)) as zf:
        manifest_text = zf.read("corpus/manifest.csv").decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(manifest_text)))
    owners = {row["owner"] for row in rows}
    # Fixture cast always returns Alice Fixture, Bob Fixture, Carol Fixture — not Holmes/Watson.
    for holmes_name in ("Sherlock Holmes", "Dr. Watson", "Irene Adler", "Mycroft Holmes"):
        assert holmes_name not in owners, f"hardcoded persona {holmes_name!r} found in manifest"


def test_fixture_cast_personas_appear_in_manifest():
    """Fixture LLM returns Alice/Bob/Carol; they should appear as owners in manifest.csv."""
    events, result = _run()
    assert result is not None
    with zipfile.ZipFile(io.BytesIO(result.zip_bytes)) as zf:
        manifest_text = zf.read("corpus/manifest.csv").decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(manifest_text)))
    owners = {row["owner"] for row in rows}
    fixture_names = {"Alice Fixture", "Bob Fixture", "Carol Fixture"}
    assert owners & fixture_names, f"no fixture persona found in manifest owners: {owners}"


def test_system_actors_still_appear_in_manifest():
    """System actor artifacts (building-controller, phone-exchange) still present."""
    events, result = _run()
    assert result is not None
    with zipfile.ZipFile(io.BytesIO(result.zip_bytes)) as zf:
        manifest_text = zf.read("corpus/manifest.csv").decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(manifest_text)))
    devices = {row["device"] for row in rows}
    assert "building-controller" in devices, "building-controller device missing from manifest"
    assert "phone-exchange" in devices, "phone-exchange device missing from manifest"


