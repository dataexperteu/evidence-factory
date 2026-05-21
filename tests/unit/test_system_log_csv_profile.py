"""Unit tests for the API-layer system_log_csv profile writer.

Acceptance criteria verified here:
- access_log schema: timestamp, controller_id, badge_id, persona_id, door_id, granted
- cdr schema: call_id, start_time, end_time, calling_persona_id, called_persona_id,
              calling_number, called_number, direction
- Timestamps/start_times are monotone (ISO-8601 lexicographic sort matches time order)
- Rows fall within the bounding event's timestamp window
- SHA-256 of payload is stable across two identical writes
"""

from __future__ import annotations

import csv
import hashlib
import io
from datetime import UTC, datetime

import pytest

from api.provenance.system_log_csv_profile import SystemLogBrief, write_system_log_csv

_TS = datetime(2024, 6, 3, 9, 0, 0, tzinfo=UTC)
_PERSONA_IDS = ("p_holmes", "p_lestrade", "p_watson", "p_hudson")

DISCLAIMER = "SYNTHETIC EVIDENCE — for demonstration only"


def _brief(**overrides: object) -> SystemLogBrief:
    defaults = dict(
        schema="access_log",
        device_id="d_bldg_ctrl",
        event_id="ev_sys_1",
        event_timestamp=_TS,
        persona_ids=_PERSONA_IDS,
    )
    defaults.update(overrides)
    return SystemLogBrief(**defaults)  # type: ignore[arg-type]


def _parse(raw: bytes) -> tuple[list[dict[str, str]], list[str]]:
    decoded = raw.decode("utf-8")
    reader = csv.DictReader(io.StringIO(decoded))
    rows = list(reader)
    return rows, list(reader.fieldnames or [])


# ---------------------------------------------------------------------------
# access_log schema
# ---------------------------------------------------------------------------


def test_access_log_required_columns() -> None:
    out = write_system_log_csv(_brief(schema="access_log"), disclaimer=DISCLAIMER)
    rows, fieldnames = _parse(out.payload)
    required = {"timestamp", "controller_id", "badge_id", "persona_id", "door_id", "granted"}
    assert required.issubset(set(fieldnames)), f"missing: {required - set(fieldnames)}"


def test_access_log_has_one_row_per_persona() -> None:
    out = write_system_log_csv(_brief(schema="access_log"), disclaimer=DISCLAIMER)
    rows, _ = _parse(out.payload)
    assert len(rows) == len(_PERSONA_IDS)


def test_access_log_timestamp_monotonicity() -> None:
    out = write_system_log_csv(_brief(schema="access_log"), disclaimer=DISCLAIMER)
    rows, _ = _parse(out.payload)
    timestamps = [r["timestamp"] for r in rows]
    assert timestamps == sorted(timestamps), f"timestamps not monotone: {timestamps}"


def test_access_log_timestamps_within_event_window() -> None:
    """Row timestamps must fall at or after event_timestamp (we never go before)."""
    out = write_system_log_csv(_brief(schema="access_log"), disclaimer=DISCLAIMER)
    rows, _ = _parse(out.payload)
    event_iso = _TS.strftime("%Y-%m-%dT%H:%M:%S")
    for row in rows:
        assert row["timestamp"] >= event_iso, (
            f"timestamp {row['timestamp']} is before event window {event_iso}"
        )


def test_access_log_persona_ids_are_registry_subjects() -> None:
    out = write_system_log_csv(_brief(schema="access_log"), disclaimer=DISCLAIMER)
    rows, _ = _parse(out.payload)
    found = {r["persona_id"] for r in rows}
    assert found == set(_PERSONA_IDS)


def test_access_log_sha256_stable_across_two_writes() -> None:
    b = _brief(schema="access_log")
    h1 = hashlib.sha256(write_system_log_csv(b, disclaimer=DISCLAIMER).payload).hexdigest()
    h2 = hashlib.sha256(write_system_log_csv(b, disclaimer=DISCLAIMER).payload).hexdigest()
    assert h1 == h2, "SHA-256 must be stable across identical writes"


# ---------------------------------------------------------------------------
# cdr schema
# ---------------------------------------------------------------------------


def test_cdr_required_columns() -> None:
    out = write_system_log_csv(_brief(schema="cdr"), disclaimer=DISCLAIMER)
    rows, fieldnames = _parse(out.payload)
    required = {
        "call_id", "start_time", "end_time",
        "calling_persona_id", "called_persona_id",
        "calling_number", "called_number", "direction",
    }
    assert required.issubset(set(fieldnames)), f"missing: {required - set(fieldnames)}"


def test_cdr_start_time_monotonicity() -> None:
    out = write_system_log_csv(_brief(schema="cdr"), disclaimer=DISCLAIMER)
    rows, _ = _parse(out.payload)
    start_times = [r["start_time"] for r in rows]
    assert start_times == sorted(start_times), f"start_times not monotone: {start_times}"


def test_cdr_start_times_within_event_window() -> None:
    out = write_system_log_csv(_brief(schema="cdr"), disclaimer=DISCLAIMER)
    rows, _ = _parse(out.payload)
    event_iso = _TS.strftime("%Y-%m-%dT%H:%M:%S")
    for row in rows:
        assert row["start_time"] >= event_iso


def test_cdr_sha256_stable_across_two_writes() -> None:
    b = _brief(schema="cdr")
    h1 = hashlib.sha256(write_system_log_csv(b, disclaimer=DISCLAIMER).payload).hexdigest()
    h2 = hashlib.sha256(write_system_log_csv(b, disclaimer=DISCLAIMER).payload).hexdigest()
    assert h1 == h2, "SHA-256 must be stable across identical CDR writes"


def test_cdr_end_time_after_start_time() -> None:
    out = write_system_log_csv(_brief(schema="cdr"), disclaimer=DISCLAIMER)
    rows, _ = _parse(out.payload)
    for row in rows:
        assert row["end_time"] >= row["start_time"]


# ---------------------------------------------------------------------------
# Validation guards
# ---------------------------------------------------------------------------


def test_requires_timezone_aware_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        write_system_log_csv(
            _brief(event_timestamp=datetime(2024, 6, 3, 9, 0, 0)),  # naive
            disclaimer=DISCLAIMER,
        )


def test_requires_at_least_one_persona_id() -> None:
    with pytest.raises(ValueError, match="persona_id"):
        write_system_log_csv(_brief(persona_ids=()), disclaimer=DISCLAIMER)


# ---------------------------------------------------------------------------
# Output format
# ---------------------------------------------------------------------------


def test_filename_ends_with_csv() -> None:
    out = write_system_log_csv(_brief(), disclaimer=DISCLAIMER)
    assert out.filename.endswith(".csv")


def test_filename_is_filesystem_safe() -> None:
    out = write_system_log_csv(_brief(), disclaimer=DISCLAIMER)
    assert "/" not in out.filename
    assert ":" not in out.filename


def test_sha256_matches_payload() -> None:
    out = write_system_log_csv(_brief(), disclaimer=DISCLAIMER)
    assert hashlib.sha256(out.payload).hexdigest() == out.sha256


def test_payload_is_utf8_decodable() -> None:
    out = write_system_log_csv(_brief(), disclaimer=DISCLAIMER)
    decoded = out.payload.decode("utf-8")
    assert decoded.strip()
