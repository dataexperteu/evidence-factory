"""Provenance Catalog — SMS/chat export profile golden round-trip tests.

Acceptance criteria exercised:
- CSV round-trip via csv.DictReader: schema + thread integrity
- SQLite round-trip via sqlite3: schema + thread integrity
- Multi-participant copies: same thread, two owners, timestamps adjusted for receiver
- Monotone timestamp validation
- Window boundary validation
- Filename is filesystem-safe
- SHA-256 matches payload bytes
"""

from __future__ import annotations

import csv
import hashlib
import io
import sqlite3
from datetime import UTC, datetime
from typing import Any

import pytest

from api.provenance.sms_profile import (
    DELIVERY_DELTA,
    SmsBrief,
    SmsMessage,
    write_sms_csv,
    write_sms_sqlite,
)

_WINDOW_START = datetime(2024, 6, 3, 9, 0, 0, tzinfo=UTC)
_WINDOW_END = datetime(2024, 6, 3, 9, 30, 0, tzinfo=UTC)
_TS1 = datetime(2024, 6, 3, 9, 5, 0, tzinfo=UTC)
_TS2 = datetime(2024, 6, 3, 9, 10, 0, tzinfo=UTC)

DISCLAIMER = "SYNTHETIC EVIDENCE — demo only"


def _brief(**overrides: Any) -> SmsBrief:
    defaults: dict[str, Any] = dict(
        thread_id="thread-001",
        participants=("Alice", "Bob"),
        messages=(
            SmsMessage(
                sender="Alice",
                recipient="Bob",
                timestamp_iso=_TS1.isoformat(),
                body="Hey, did you see the report?",
            ),
            SmsMessage(
                sender="Bob",
                recipient="Alice",
                timestamp_iso=_TS2.isoformat(),
                body="Yes, I reviewed it this morning.",
            ),
        ),
        window_start=_WINDOW_START,
        window_end=_WINDOW_END,
    )
    defaults.update(overrides)
    return SmsBrief(**defaults)


# ---------------------------------------------------------------------------
# CSV round-trip
# ---------------------------------------------------------------------------


def test_csv_round_trip_schema():
    """Re-parsing with csv.DictReader must recover all expected columns."""
    out = write_sms_csv(_brief(), owner="Alice", disclaimer=DISCLAIMER)
    rows = list(csv.DictReader(io.StringIO(out.payload.decode("utf-8"))))
    assert len(rows) == 2
    expected_cols = {"thread_id", "sender", "recipient", "timestamp_iso", "body"}
    assert set(rows[0].keys()) == expected_cols


def test_csv_round_trip_thread_integrity():
    """All rows carry the same thread_id; sender/recipient/body match the brief."""
    out = write_sms_csv(_brief(), owner="Alice", disclaimer=DISCLAIMER)
    rows = list(csv.DictReader(io.StringIO(out.payload.decode("utf-8"))))
    assert all(r["thread_id"] == "thread-001" for r in rows)
    assert rows[0]["sender"] == "Alice"
    assert rows[0]["recipient"] == "Bob"
    assert rows[1]["sender"] == "Bob"
    assert rows[1]["recipient"] == "Alice"
    assert "report" in rows[0]["body"]
    assert "morning" in rows[1]["body"]


def test_csv_sha256_matches_payload():
    out = write_sms_csv(_brief(), owner="Alice", disclaimer=DISCLAIMER)
    assert hashlib.sha256(out.payload).hexdigest() == out.sha256


def test_csv_filename_is_filesystem_safe():
    out = write_sms_csv(_brief(), owner="Alice", disclaimer=DISCLAIMER)
    assert "/" not in out.filename
    assert ":" not in out.filename
    assert out.filename.endswith(".csv")


# ---------------------------------------------------------------------------
# SQLite round-trip
# ---------------------------------------------------------------------------


def test_sqlite_round_trip_schema():
    """Re-parsing with sqlite3 must expose threads + messages tables with correct columns."""
    out = write_sms_sqlite(_brief(), owner="Alice", disclaimer=DISCLAIMER)
    conn = sqlite3.connect(":memory:")
    conn.deserialize(out.payload)  # type: ignore[attr-defined]
    cur = conn.execute("PRAGMA table_info(threads)")
    thread_cols = {row[1] for row in cur.fetchall()}
    assert {"thread_id", "owner", "disclaimer"} <= thread_cols
    cur = conn.execute("PRAGMA table_info(messages)")
    msg_cols = {row[1] for row in cur.fetchall()}
    assert {"thread_id", "sender", "recipient", "sent_iso", "received_iso", "body"} <= msg_cols
    conn.close()


def test_sqlite_round_trip_thread_integrity():
    out = write_sms_sqlite(_brief(), owner="Alice", disclaimer=DISCLAIMER)
    conn = sqlite3.connect(":memory:")
    conn.deserialize(out.payload)  # type: ignore[attr-defined]
    rows = conn.execute(
        "SELECT sender, recipient, sent_iso, body FROM messages ORDER BY id"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0][0] == "Alice"
    assert rows[1][0] == "Bob"
    thread_row = conn.execute("SELECT thread_id, owner FROM threads").fetchone()
    assert thread_row[0] == "thread-001"
    assert thread_row[1] == "Alice"
    conn.close()


def test_sqlite_disclaimer_stored_in_threads_table():
    out = write_sms_sqlite(_brief(), owner="Alice", disclaimer=DISCLAIMER)
    conn = sqlite3.connect(":memory:")
    conn.deserialize(out.payload)  # type: ignore[attr-defined]
    row = conn.execute("SELECT disclaimer FROM threads").fetchone()
    assert row[0] == DISCLAIMER
    conn.close()


def test_sqlite_sha256_matches_payload():
    out = write_sms_sqlite(_brief(), owner="Alice", disclaimer=DISCLAIMER)
    assert hashlib.sha256(out.payload).hexdigest() == out.sha256


def test_sqlite_filename_is_filesystem_safe():
    out = write_sms_sqlite(_brief(), owner="Alice", disclaimer=DISCLAIMER)
    assert "/" not in out.filename
    assert out.filename.endswith(".db")


# ---------------------------------------------------------------------------
# Multi-participant copies: sender vs. receiver perspective
# ---------------------------------------------------------------------------


def test_csv_sender_perspective_uses_sent_timestamps():
    """Sender's copy: all timestamp_iso values match the original sent times."""
    out = write_sms_csv(_brief(), owner="Alice", disclaimer=DISCLAIMER)
    rows = list(csv.DictReader(io.StringIO(out.payload.decode("utf-8"))))
    # Alice sent message 0 → unchanged
    assert rows[0]["timestamp_iso"] == _TS1.isoformat()
    # Alice received message 1 → adjusted by DELIVERY_DELTA
    expected_received = (_TS2 + DELIVERY_DELTA).isoformat()
    assert rows[1]["timestamp_iso"] == expected_received


def test_csv_receiver_perspective_adjusts_received_timestamps():
    """Bob's copy: messages Bob received have timestamp_iso = sent + DELIVERY_DELTA."""
    out = write_sms_csv(_brief(), owner="Bob", disclaimer=DISCLAIMER)
    rows = list(csv.DictReader(io.StringIO(out.payload.decode("utf-8"))))
    # Bob received message 0 (Alice sent) → adjusted
    expected_received = (_TS1 + DELIVERY_DELTA).isoformat()
    assert rows[0]["timestamp_iso"] == expected_received
    # Bob sent message 1 → unchanged
    assert rows[1]["timestamp_iso"] == _TS2.isoformat()


def test_multi_participant_copies_have_consistent_content():
    """Sender and receiver copies carry the same thread_id, senders, and bodies."""
    brief = _brief()
    alice_out = write_sms_csv(brief, owner="Alice", disclaimer=DISCLAIMER)
    bob_out = write_sms_csv(brief, owner="Bob", disclaimer=DISCLAIMER)

    alice_rows = list(csv.DictReader(io.StringIO(alice_out.payload.decode("utf-8"))))
    bob_rows = list(csv.DictReader(io.StringIO(bob_out.payload.decode("utf-8"))))

    assert len(alice_rows) == len(bob_rows)
    for a_row, b_row in zip(alice_rows, bob_rows, strict=True):
        assert a_row["thread_id"] == b_row["thread_id"]
        assert a_row["sender"] == b_row["sender"]
        assert a_row["recipient"] == b_row["recipient"]
        assert a_row["body"] == b_row["body"]


def test_sqlite_multi_participant_received_iso_adjusted():
    """In the SQLite representation received_iso is adjusted for the recipient."""
    brief = _brief()
    alice_out = write_sms_sqlite(brief, owner="Alice", disclaimer=DISCLAIMER)
    bob_out = write_sms_sqlite(brief, owner="Bob", disclaimer=DISCLAIMER)

    def _messages(payload: bytes) -> list[tuple]:
        conn = sqlite3.connect(":memory:")
        conn.deserialize(payload)  # type: ignore[attr-defined]
        rows = conn.execute(
            "SELECT sender, sent_iso, received_iso FROM messages ORDER BY id"
        ).fetchall()
        conn.close()
        return rows

    alice_msgs = _messages(alice_out.payload)
    bob_msgs = _messages(bob_out.payload)

    # Alice sent msg 0: received_iso == sent_iso on Alice's copy
    assert alice_msgs[0][1] == alice_msgs[0][2]
    # Bob received msg 0: received_iso > sent_iso on Bob's copy
    assert bob_msgs[0][2] == (_TS1 + DELIVERY_DELTA).isoformat()

    # Bob sent msg 1: received_iso == sent_iso on Bob's copy
    assert bob_msgs[1][1] == bob_msgs[1][2]
    # Alice received msg 1: received_iso > sent_iso on Alice's copy
    assert alice_msgs[1][2] == (_TS2 + DELIVERY_DELTA).isoformat()


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


def test_rejects_empty_message_list():
    with pytest.raises(ValueError, match="at least one message"):
        write_sms_csv(_brief(messages=()), owner="Alice", disclaimer=DISCLAIMER)


def test_rejects_naive_window_timestamps():
    with pytest.raises(ValueError, match="timezone-aware"):
        write_sms_csv(
            _brief(window_start=datetime(2024, 6, 3, 9, 0, 0)),
            owner="Alice",
            disclaimer=DISCLAIMER,
        )


def test_rejects_naive_message_timestamp():
    bad_msg = SmsMessage(
        sender="Alice", recipient="Bob", timestamp_iso="2024-06-03T09:05:00", body="hi"
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        write_sms_csv(
            _brief(messages=(bad_msg,)),
            owner="Alice",
            disclaimer=DISCLAIMER,
        )


def test_rejects_non_monotone_timestamps():
    msg1 = SmsMessage(
        sender="Alice",
        recipient="Bob",
        timestamp_iso=_TS2.isoformat(),
        body="hi",
    )
    msg2 = SmsMessage(
        sender="Bob",
        recipient="Alice",
        timestamp_iso=_TS1.isoformat(),  # earlier than msg1
        body="hello",
    )
    with pytest.raises(ValueError, match="monotonically non-decreasing"):
        write_sms_csv(_brief(messages=(msg1, msg2)), owner="Alice", disclaimer=DISCLAIMER)


def test_rejects_timestamp_outside_window():
    outside = datetime(2024, 6, 3, 10, 0, 0, tzinfo=UTC)  # after window_end
    bad_msg = SmsMessage(
        sender="Alice", recipient="Bob", timestamp_iso=outside.isoformat(), body="hi"
    )
    with pytest.raises(ValueError, match="outside event window"):
        write_sms_csv(
            _brief(messages=(bad_msg,)),
            owner="Alice",
            disclaimer=DISCLAIMER,
        )


# ---------------------------------------------------------------------------
# format / thread_id fields
# ---------------------------------------------------------------------------


def test_csv_format_field():
    out = write_sms_csv(_brief(), owner="Alice", disclaimer=DISCLAIMER)
    assert out.format == "csv"
    assert out.thread_id == "thread-001"
    assert out.owner == "Alice"


def test_sqlite_format_field():
    out = write_sms_sqlite(_brief(), owner="Alice", disclaimer=DISCLAIMER)
    assert out.format == "sqlite"
    assert out.thread_id == "thread-001"
    assert out.owner == "Alice"


def test_single_message_thread_passes_validation():
    brief = _brief(
        messages=(
            SmsMessage(
                sender="Alice",
                recipient="Bob",
                timestamp_iso=_TS1.isoformat(),
                body="Just one message.",
            ),
        )
    )
    out = write_sms_csv(brief, owner="Alice", disclaimer=DISCLAIMER)
    rows = list(csv.DictReader(io.StringIO(out.payload.decode("utf-8"))))
    assert len(rows) == 1
    assert rows[0]["body"] == "Just one message."


def test_window_start_equals_end_single_message():
    ts = datetime(2024, 6, 3, 9, 5, 0, tzinfo=UTC)
    brief = SmsBrief(
        thread_id="t",
        participants=("A", "B"),
        messages=(SmsMessage(sender="A", recipient="B", timestamp_iso=ts.isoformat(), body="x"),),
        window_start=ts,
        window_end=ts,
    )
    out = write_sms_csv(brief, owner="A", disclaimer=DISCLAIMER)
    rows = list(csv.DictReader(io.StringIO(out.payload.decode("utf-8"))))
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Same timestamp (equal) is allowed (non-strict monotone)
# ---------------------------------------------------------------------------


def test_equal_timestamps_allowed():
    msg1 = SmsMessage(sender="Alice", recipient="Bob", timestamp_iso=_TS1.isoformat(), body="a")
    msg2 = SmsMessage(sender="Bob", recipient="Alice", timestamp_iso=_TS1.isoformat(), body="b")
    out = write_sms_csv(_brief(messages=(msg1, msg2)), owner="Alice", disclaimer=DISCLAIMER)
    rows = list(csv.DictReader(io.StringIO(out.payload.decode("utf-8"))))
    assert len(rows) == 2
