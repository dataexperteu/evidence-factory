"""Provenance Catalog — system_log_csv profile golden round-trip.

Tests both access_log and cdr variants via csv.DictReader (as the acceptance
criteria specifies). Asserts:
  - correct schema columns
  - timestamp monotonicity of output rows
  - SHA-256 stability across two writes with identical inputs
  - rejection of non-monotone rows
  - rejection of rows falling outside the window
  - rejection of empty row sets
  - rejection of timezone-naive timestamps
"""

import csv
import hashlib
import io
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from api.provenance.system_log_csv_profile import (
    AccessLogBrief,
    AccessLogRow,
    CdrBrief,
    CdrRow,
    write_access_log,
    write_cdr,
)

_BASE = datetime(2024, 6, 3, 9, 0, 0, tzinfo=UTC)
_WINDOW_START = _BASE
_WINDOW_END = _BASE + timedelta(hours=8)

# ── access_log helpers ──────────────────────────────────────────────────────


def _make_access_rows(n: int = 3) -> tuple[AccessLogRow, ...]:
    return tuple(
        AccessLogRow(
            timestamp=_BASE + timedelta(minutes=i * 20),
            controller_id="sys_bac_221b",
            badge_id=f"badge_{i:04d}",
            persona_id=f"p_person_{i}",
            door_id="main-entrance",
            granted=(i % 2 == 0),
        )
        for i in range(n)
    )


def _access_brief(**overrides: Any) -> AccessLogBrief:
    defaults: dict[str, Any] = dict(
        system_id="sys_bac_221b",
        rows=_make_access_rows(),
        window_start=_WINDOW_START,
        window_end=_WINDOW_END,
    )
    defaults.update(overrides)
    return AccessLogBrief(**defaults)


# ── cdr helpers ─────────────────────────────────────────────────────────────


def _make_cdr_rows(n: int = 2) -> tuple[CdrRow, ...]:
    return tuple(
        CdrRow(
            call_id=f"call_{i:04d}",
            start_time=_BASE + timedelta(minutes=i * 30),
            end_time=_BASE + timedelta(minutes=i * 30 + 5),
            calling_persona_id="p_holmes",
            called_persona_id="p_watson",
            calling_number="+44-20-7946-0001",
            called_number="+44-20-7946-0002",
            direction="outbound",
        )
        for i in range(n)
    )


def _cdr_brief(**overrides: Any) -> CdrBrief:
    defaults: dict[str, Any] = dict(
        system_id="sys_pbx_baker",
        rows=_make_cdr_rows(),
        window_start=_WINDOW_START,
        window_end=_WINDOW_END,
    )
    defaults.update(overrides)
    return CdrBrief(**defaults)


# ════════════════════════════════════════════════════════════════════════════
# access_log tests
# ════════════════════════════════════════════════════════════════════════════


def test_access_log_schema():
    out = write_access_log(_access_brief(), disclaimer="SYNTHETIC EVIDENCE")
    rows = list(csv.DictReader(io.StringIO(out.payload.decode("utf-8"))))
    assert rows, "expected at least one data row"
    assert set(rows[0].keys()) == {
        "timestamp",
        "controller_id",
        "badge_id",
        "persona_id",
        "door_id",
        "granted",
    }


def test_access_log_timestamp_monotonicity():
    out = write_access_log(_access_brief(), disclaimer="d")
    rows = list(csv.DictReader(io.StringIO(out.payload.decode("utf-8"))))
    timestamps = [datetime.fromisoformat(r["timestamp"]) for r in rows]
    for i in range(1, len(timestamps)):
        assert timestamps[i] >= timestamps[i - 1], (
            f"access_log row {i} timestamp {timestamps[i]} precedes row {i - 1} {timestamps[i-1]}"
        )


def test_access_log_timestamps_are_iso8601():
    out = write_access_log(_access_brief(), disclaimer="d")
    rows = list(csv.DictReader(io.StringIO(out.payload.decode("utf-8"))))
    for row in rows:
        ts = row["timestamp"]
        assert ts.endswith("Z"), f"timestamp {ts!r} is not UTC ISO-8601"
        datetime.fromisoformat(ts.replace("Z", "+00:00"))  # must parse


def test_access_log_granted_values():
    out = write_access_log(_access_brief(), disclaimer="d")
    rows = list(csv.DictReader(io.StringIO(out.payload.decode("utf-8"))))
    for row in rows:
        assert row["granted"] in ("true", "false")


def test_access_log_sha256_matches_payload():
    out = write_access_log(_access_brief(), disclaimer="d")
    assert hashlib.sha256(out.payload).hexdigest() == out.sha256


def test_access_log_sha256_stability_across_two_writes():
    brief = _access_brief()
    out1 = write_access_log(brief, disclaimer="d")
    out2 = write_access_log(brief, disclaimer="d")
    assert out1.sha256 == out2.sha256, "payload sha256 must be identical for identical inputs"
    # Filenames carry a random suffix and will differ:
    assert out1.filename != out2.filename


def test_access_log_variant_field():
    out = write_access_log(_access_brief(), disclaimer="d")
    assert out.variant == "access_log"


def test_access_log_filename_is_filesystem_safe():
    out = write_access_log(_access_brief(), disclaimer="d")
    assert "/" not in out.filename
    assert out.filename.endswith(".csv")


def test_access_log_rejects_empty_rows():
    with pytest.raises(ValueError, match="at least one row"):
        write_access_log(_access_brief(rows=()), disclaimer="d")


def test_access_log_rejects_non_monotone_rows():
    rows = list(_make_access_rows(3))
    rows[0], rows[1] = rows[1], rows[0]  # swap first two → non-monotone
    with pytest.raises(ValueError, match="monotone"):
        write_access_log(_access_brief(rows=tuple(rows)), disclaimer="d")


def test_access_log_rejects_row_outside_window():
    bad_row = AccessLogRow(
        timestamp=_WINDOW_END + timedelta(minutes=1),  # after window
        controller_id="c",
        badge_id="b",
        persona_id="p",
        door_id="d",
        granted=True,
    )
    with pytest.raises(ValueError, match="outside window"):
        write_access_log(_access_brief(rows=(bad_row,)), disclaimer="d")


def test_access_log_rejects_naive_window_timestamps():
    with pytest.raises(ValueError, match="timezone-aware"):
        write_access_log(
            _access_brief(window_start=datetime(2024, 6, 3, 9, 0, 0)),
            disclaimer="d",
        )


def test_access_log_rejects_naive_row_timestamp():
    bad_row = AccessLogRow(
        timestamp=datetime(2024, 6, 3, 9, 0, 0),  # naive
        controller_id="c",
        badge_id="b",
        persona_id="p",
        door_id="d",
        granted=True,
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        write_access_log(_access_brief(rows=(bad_row,)), disclaimer="d")


# ════════════════════════════════════════════════════════════════════════════
# cdr tests
# ════════════════════════════════════════════════════════════════════════════


def test_cdr_schema():
    out = write_cdr(_cdr_brief(), disclaimer="SYNTHETIC EVIDENCE")
    rows = list(csv.DictReader(io.StringIO(out.payload.decode("utf-8"))))
    assert rows, "expected at least one data row"
    assert set(rows[0].keys()) == {
        "call_id",
        "start_time",
        "end_time",
        "calling_persona_id",
        "called_persona_id",
        "calling_number",
        "called_number",
        "direction",
    }


def test_cdr_timestamp_monotonicity():
    out = write_cdr(_cdr_brief(), disclaimer="d")
    rows = list(csv.DictReader(io.StringIO(out.payload.decode("utf-8"))))
    starts = [datetime.fromisoformat(r["start_time"]) for r in rows]
    for i in range(1, len(starts)):
        assert starts[i] >= starts[i - 1], (
            f"CDR row {i} start_time {starts[i]} precedes row {i - 1} {starts[i - 1]}"
        )


def test_cdr_timestamps_are_iso8601():
    out = write_cdr(_cdr_brief(), disclaimer="d")
    rows = list(csv.DictReader(io.StringIO(out.payload.decode("utf-8"))))
    for row in rows:
        for field in ("start_time", "end_time"):
            ts = row[field]
            assert ts.endswith("Z"), f"{field} {ts!r} is not UTC ISO-8601"
            datetime.fromisoformat(ts.replace("Z", "+00:00"))


def test_cdr_sha256_matches_payload():
    out = write_cdr(_cdr_brief(), disclaimer="d")
    assert hashlib.sha256(out.payload).hexdigest() == out.sha256


def test_cdr_sha256_stability_across_two_writes():
    brief = _cdr_brief()
    out1 = write_cdr(brief, disclaimer="d")
    out2 = write_cdr(brief, disclaimer="d")
    assert out1.sha256 == out2.sha256, "payload sha256 must be identical for identical inputs"
    assert out1.filename != out2.filename


def test_cdr_variant_field():
    out = write_cdr(_cdr_brief(), disclaimer="d")
    assert out.variant == "cdr"


def test_cdr_filename_is_filesystem_safe():
    out = write_cdr(_cdr_brief(), disclaimer="d")
    assert "/" not in out.filename
    assert out.filename.endswith(".csv")


def test_cdr_rejects_empty_rows():
    with pytest.raises(ValueError, match="at least one row"):
        write_cdr(_cdr_brief(rows=()), disclaimer="d")


def test_cdr_rejects_non_monotone_rows():
    rows = list(_make_cdr_rows(3))
    rows[0], rows[1] = rows[1], rows[0]
    with pytest.raises(ValueError, match="monotone"):
        write_cdr(_cdr_brief(rows=tuple(rows)), disclaimer="d")


def test_cdr_rejects_row_outside_window():
    bad_row = CdrRow(
        call_id="c0",
        start_time=_WINDOW_END,
        end_time=_WINDOW_END + timedelta(minutes=5),  # end exceeds window
        calling_persona_id="p_a",
        called_persona_id="p_b",
        calling_number="+1",
        called_number="+2",
        direction="inbound",
    )
    with pytest.raises(ValueError, match="outside window"):
        write_cdr(_cdr_brief(rows=(bad_row,)), disclaimer="d")


def test_cdr_rejects_naive_window_timestamps():
    with pytest.raises(ValueError, match="timezone-aware"):
        write_cdr(
            _cdr_brief(window_start=datetime(2024, 6, 3, 9, 0, 0)),
            disclaimer="d",
        )


def test_cdr_rejects_naive_row_timestamp():
    bad_row = CdrRow(
        call_id="c0",
        start_time=datetime(2024, 6, 3, 9, 0, 0),  # naive
        end_time=_BASE + timedelta(minutes=5),
        calling_persona_id="p_a",
        called_persona_id="p_b",
        calling_number="+1",
        called_number="+2",
        direction="outbound",
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        write_cdr(_cdr_brief(rows=(bad_row,)), disclaimer="d")


# ════════════════════════════════════════════════════════════════════════════
# registry persona-id validation (emitter-level, tested here for completeness)
# ════════════════════════════════════════════════════════════════════════════


def test_emitter_rejects_unknown_persona_id():
    """Validate that the emitter checks persona_ids against the registry."""
    from api.pipeline.artifact_emitter import _validate_subject_personas
    from api.pipeline.persona_registry import default_registry

    registry = default_registry()
    # Valid persona ids must not raise.
    _validate_subject_personas(registry, ("p_holmes", "p_watson"))
    # Unknown persona id must raise.
    with pytest.raises(ValueError, match="not in the registry"):
        _validate_subject_personas(registry, ("p_unknown_x",))
