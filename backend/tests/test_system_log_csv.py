"""Golden-file tests for the system_log_csv profile.

Acceptance criteria verified here:
- access_log schema: timestamp, controller_id, badge_id, persona_id, door_id, granted
- cdr schema: call_id, start_time, end_time, calling_persona_id, called_persona_id,
              calling_number, called_number, direction
- Timestamps are monotone within an emission
- SHA-256 is stable across two identical writes
- Persona IDs not in the registry are rejected (via _materialise_batch)
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from evidence_factory.models import ArtifactProfile, CaseBible
from evidence_factory.noise.generator import NoiseGenerator
from evidence_factory.noise.guard import LeakContradictGuard
from evidence_factory.provenance.catalog import ProvenanceCatalog
from evidence_factory.registry import PersonaRegistry


@pytest.fixture()
def catalog() -> ProvenanceCatalog:
    return ProvenanceCatalog()


# ---------------------------------------------------------------------------
# access_log golden-file: schema, monotonicity, SHA-256 stability
# ---------------------------------------------------------------------------

_ACCESS_LOG_METADATA: dict[str, Any] = {
    "schema": "access_log",
    "controller_id": "ctrl_entrance_1",
    "entries": [
        {
            "timestamp": "2024-01-15T08:00:00",
            "badge_id": "badge_alice",
            "persona_id": "alice",
            "door_id": "door_main",
            "granted": "true",
        },
        {
            "timestamp": "2024-01-15T08:05:00",
            "badge_id": "badge_bob",
            "persona_id": "bob",
            "door_id": "door_main",
            "granted": "true",
        },
        {
            "timestamp": "2024-01-15T08:10:00",
            "badge_id": "badge_alice",
            "persona_id": "alice",
            "door_id": "door_server",
            "granted": "false",
        },
    ],
}


def _parse_rows(raw: bytes) -> tuple[list[dict[str, str]], list[str]]:
    decoded = raw.decode("utf-8")
    reader = csv.DictReader(io.StringIO(decoded))
    rows = list(reader)
    fieldnames = list(reader.fieldnames or [])
    return rows, fieldnames


def test_access_log_schema_columns(catalog: ProvenanceCatalog) -> None:
    raw = catalog.write(ArtifactProfile.SYSTEM_LOG_CSV, "", _ACCESS_LOG_METADATA)
    rows, fieldnames = _parse_rows(raw)
    assert rows, "expected at least one data row"
    required = {"timestamp", "controller_id", "badge_id", "persona_id", "door_id", "granted"}
    assert required.issubset(set(fieldnames)), f"missing columns: {required - set(fieldnames)}"


def test_access_log_timestamp_monotonicity(catalog: ProvenanceCatalog) -> None:
    raw = catalog.write(ArtifactProfile.SYSTEM_LOG_CSV, "", _ACCESS_LOG_METADATA)
    rows, _ = _parse_rows(raw)
    timestamps = [r["timestamp"] for r in rows]
    assert timestamps == sorted(timestamps), f"timestamps not monotone: {timestamps}"


def test_access_log_sha256_stable_across_two_writes(catalog: ProvenanceCatalog) -> None:
    raw1 = catalog.write(ArtifactProfile.SYSTEM_LOG_CSV, "", _ACCESS_LOG_METADATA)
    raw2 = catalog.write(ArtifactProfile.SYSTEM_LOG_CSV, "", _ACCESS_LOG_METADATA)
    h1 = hashlib.sha256(raw1).hexdigest()
    h2 = hashlib.sha256(raw2).hexdigest()
    assert h1 == h2, "SHA-256 must be stable across identical writes"


def test_access_log_entry_values_present(catalog: ProvenanceCatalog) -> None:
    raw = catalog.write(ArtifactProfile.SYSTEM_LOG_CSV, "", _ACCESS_LOG_METADATA)
    rows, _ = _parse_rows(raw)
    persona_ids = {r["persona_id"] for r in rows}
    assert "alice" in persona_ids
    assert "bob" in persona_ids
    assert all(r["controller_id"] == "ctrl_entrance_1" for r in rows)


# ---------------------------------------------------------------------------
# cdr golden-file: schema, monotonicity, SHA-256 stability
# ---------------------------------------------------------------------------

_CDR_METADATA: dict[str, Any] = {
    "schema": "cdr",
    "entries": [
        {
            "call_id": "call_001",
            "start_time": "2024-01-15T09:00:00",
            "end_time": "2024-01-15T09:08:00",
            "calling_persona_id": "alice",
            "called_persona_id": "bob",
            "calling_number": "+1-555-0101",
            "called_number": "+1-555-0102",
            "direction": "outbound",
        },
        {
            "call_id": "call_002",
            "start_time": "2024-01-15T09:15:00",
            "end_time": "2024-01-15T09:20:00",
            "calling_persona_id": "bob",
            "called_persona_id": "alice",
            "calling_number": "+1-555-0102",
            "called_number": "+1-555-0101",
            "direction": "outbound",
        },
    ],
}


def test_cdr_schema_columns(catalog: ProvenanceCatalog) -> None:
    raw = catalog.write(ArtifactProfile.SYSTEM_LOG_CSV, "", _CDR_METADATA)
    rows, fieldnames = _parse_rows(raw)
    assert rows, "expected at least one data row"
    required = {
        "call_id", "start_time", "end_time",
        "calling_persona_id", "called_persona_id",
        "calling_number", "called_number", "direction",
    }
    assert required.issubset(set(fieldnames)), f"missing columns: {required - set(fieldnames)}"


def test_cdr_start_time_monotonicity(catalog: ProvenanceCatalog) -> None:
    raw = catalog.write(ArtifactProfile.SYSTEM_LOG_CSV, "", _CDR_METADATA)
    rows, _ = _parse_rows(raw)
    start_times = [r["start_time"] for r in rows]
    assert start_times == sorted(start_times), f"start_times not monotone: {start_times}"


def test_cdr_sha256_stable_across_two_writes(catalog: ProvenanceCatalog) -> None:
    raw1 = catalog.write(ArtifactProfile.SYSTEM_LOG_CSV, "", _CDR_METADATA)
    raw2 = catalog.write(ArtifactProfile.SYSTEM_LOG_CSV, "", _CDR_METADATA)
    h1 = hashlib.sha256(raw1).hexdigest()
    h2 = hashlib.sha256(raw2).hexdigest()
    assert h1 == h2, "SHA-256 must be stable across identical CDR writes"


def test_cdr_entry_values_present(catalog: ProvenanceCatalog) -> None:
    raw = catalog.write(ArtifactProfile.SYSTEM_LOG_CSV, "", _CDR_METADATA)
    rows, _ = _parse_rows(raw)
    calling_ids = {r["calling_persona_id"] for r in rows}
    assert "alice" in calling_ids
    assert "bob" in calling_ids


# ---------------------------------------------------------------------------
# Out-of-order entries are sorted into monotone order by the writer
# ---------------------------------------------------------------------------

def test_access_log_out_of_order_entries_sorted(catalog: ProvenanceCatalog) -> None:
    """Writer must sort entries so output is always monotone even if inputs aren't."""
    metadata: dict[str, Any] = {
        "schema": "access_log",
        "controller_id": "ctrl_main",
        "entries": [
            {"timestamp": "2024-01-15T08:10:00", "badge_id": "b003", "persona_id": "bob",
             "door_id": "d001", "granted": "true"},
            {"timestamp": "2024-01-15T08:00:00", "badge_id": "b001", "persona_id": "alice",
             "door_id": "d001", "granted": "true"},
            {"timestamp": "2024-01-15T08:05:00", "badge_id": "b002", "persona_id": "alice",
             "door_id": "d002", "granted": "false"},
        ],
    }
    raw = catalog.write(ArtifactProfile.SYSTEM_LOG_CSV, "", metadata)
    rows, _ = _parse_rows(raw)
    timestamps = [r["timestamp"] for r in rows]
    assert timestamps == sorted(timestamps)


# ---------------------------------------------------------------------------
# Persona-ID validation via _materialise_batch
# ---------------------------------------------------------------------------

def test_unknown_persona_id_entries_are_rejected(
    registry: PersonaRegistry, bible: CaseBible
) -> None:
    """_materialise_batch must filter out entries whose persona_id is not in the registry."""
    mock_guard = MagicMock(spec=LeakContradictGuard)
    mock_guard.check_batch.side_effect = lambda texts, props: [False] * len(texts)
    mock_llm = MagicMock()

    gen = NoiseGenerator(
        llm=mock_llm,
        catalog=ProvenanceCatalog(),
        registry=registry,
        guard=mock_guard,
    )

    raw_batch = [
        {
            "owner": "bob",
            "device": "bob_workstation",
            "profile": "system_log_csv",
            "text_content": "System access log.",
            "timestamp_offset_hours": 10.0,
            "metadata": {
                "schema": "access_log",
                "controller_id": "ctrl_test",
                "entries": [
                    {
                        "timestamp": "2024-01-15T08:00:00",
                        "badge_id": "badge_alice",
                        "persona_id": "alice",  # valid
                        "door_id": "door_main",
                        "granted": "true",
                    },
                    {
                        "timestamp": "2024-01-15T08:05:00",
                        "badge_id": "badge_charlie",
                        "persona_id": "charlie",  # not in registry
                        "door_id": "door_main",
                        "granted": "true",
                    },
                ],
            },
        }
    ]

    artifacts = gen._materialise_batch(raw_batch, ArtifactProfile.SYSTEM_LOG_CSV, bible)
    assert len(artifacts) == 1
    rows, _ = _parse_rows(artifacts[0].content)
    persona_ids = [r["persona_id"] for r in rows]
    assert "alice" in persona_ids, "valid persona should be present"
    assert "charlie" not in persona_ids, "unknown persona must be filtered out"


def test_all_unknown_persona_ids_skip_entire_artifact(
    registry: PersonaRegistry, bible: CaseBible
) -> None:
    """If every entry has an unknown persona_id, the artifact is dropped entirely."""
    mock_guard = MagicMock(spec=LeakContradictGuard)
    mock_guard.check_batch.side_effect = lambda texts, props: [False] * len(texts)
    mock_llm = MagicMock()

    gen = NoiseGenerator(
        llm=mock_llm,
        catalog=ProvenanceCatalog(),
        registry=registry,
        guard=mock_guard,
    )

    raw_batch = [
        {
            "owner": "bob",
            "device": "bob_workstation",
            "profile": "system_log_csv",
            "text_content": "Unknown system log.",
            "timestamp_offset_hours": 10.0,
            "metadata": {
                "schema": "access_log",
                "controller_id": "ctrl_test",
                "entries": [
                    {"timestamp": "2024-01-15T08:00:00", "badge_id": "bx",
                     "persona_id": "charlie", "door_id": "door_1", "granted": "true"},
                    {"timestamp": "2024-01-15T08:05:00", "badge_id": "by",
                     "persona_id": "dave", "door_id": "door_2", "granted": "false"},
                ],
            },
        }
    ]

    artifacts = gen._materialise_batch(raw_batch, ArtifactProfile.SYSTEM_LOG_CSV, bible)
    assert len(artifacts) == 0, "artifact with all-unknown personas must be dropped"


# ---------------------------------------------------------------------------
# Fallback: entries built from text_content when metadata has no "entries"
# ---------------------------------------------------------------------------

def test_fallback_entries_from_text_content(
    registry: PersonaRegistry, bible: CaseBible
) -> None:
    """When metadata lacks 'entries', _materialise_batch builds rows from text_content."""
    mock_guard = MagicMock(spec=LeakContradictGuard)
    mock_guard.check_batch.side_effect = lambda texts, props: [False] * len(texts)
    mock_llm = MagicMock()

    gen = NoiseGenerator(
        llm=mock_llm,
        catalog=ProvenanceCatalog(),
        registry=registry,
        guard=mock_guard,
    )

    raw_batch = [
        {
            "owner": "bob",
            "device": "bob_workstation",
            "profile": "system_log_csv",
            "text_content": "Routine access event.\nAnother access event.",
            "timestamp_offset_hours": 10.0,
            "metadata": {},  # no entries
        }
    ]

    artifacts = gen._materialise_batch(raw_batch, ArtifactProfile.SYSTEM_LOG_CSV, bible)
    assert len(artifacts) == 1
    rows, fieldnames = _parse_rows(artifacts[0].content)
    assert rows, "expected rows to be generated from text_content"
    required = {"timestamp", "controller_id", "badge_id", "persona_id", "door_id", "granted"}
    assert required.issubset(set(fieldnames))
    # All persona_ids must be valid registry personas
    valid = set(registry.persona_names())
    for row in rows:
        assert row["persona_id"] in valid, f"generated persona_id {row['persona_id']!r} not in registry"
