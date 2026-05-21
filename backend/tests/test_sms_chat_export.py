"""Golden-file and integrity tests for the sms_chat_export profile (Slice 4)."""

from __future__ import annotations

import csv
import io
import os
import random
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta

import pytest

from evidence_factory.models import ArtifactProfile, Device, Persona
from evidence_factory.provenance.catalog import ProvenanceCatalog
from evidence_factory.provenance.sms_profile import (
    ArtifactWriter,
    SmsChatExportEmitter,
    SmsChatExportWriter,
    SmsMessage,
    SmsThread,
    parse_thread_from_text,
)
from evidence_factory.registry import PersonaRegistry


@pytest.fixture()
def writer() -> SmsChatExportWriter:
    return SmsChatExportWriter()


@pytest.fixture()
def catalog() -> ProvenanceCatalog:
    return ProvenanceCatalog()


@pytest.fixture()
def two_persona_registry() -> PersonaRegistry:
    return PersonaRegistry(
        [
            Persona(
                name="alice",
                devices=[
                    Device(
                        device_id="alice_phone",
                        owner="alice",
                        permitted_profiles={
                            ArtifactProfile.SMS,
                            ArtifactProfile.SMS_CHAT_EXPORT,
                            ArtifactProfile.JPEG,
                        },
                    )
                ],
            ),
            Persona(
                name="bob",
                devices=[
                    Device(
                        device_id="bob_phone",
                        owner="bob",
                        permitted_profiles={
                            ArtifactProfile.SMS,
                            ArtifactProfile.SMS_CHAT_EXPORT,
                            ArtifactProfile.JPEG,
                        },
                    )
                ],
            ),
        ]
    )


@pytest.fixture()
def sample_thread() -> SmsThread:
    base = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
    return SmsThread(
        thread_id="thread-001",
        messages=[
            SmsMessage("alice", "bob", base.isoformat(), "Are you free for lunch?"),
            SmsMessage(
                "bob", "alice", (base + timedelta(minutes=2)).isoformat(), "Yes, noon works!"
            ),
            SmsMessage(
                "alice",
                "bob",
                (base + timedelta(minutes=4)).isoformat(),
                "Great, see you then.",
            ),
        ],
    )


# ---------------------------------------------------------------------------
# ArtifactWriter protocol conformance
# ---------------------------------------------------------------------------


def test_sms_chat_writer_implements_artifact_writer_protocol(
    writer: SmsChatExportWriter,
) -> None:
    assert isinstance(writer, ArtifactWriter)


# ---------------------------------------------------------------------------
# CSV golden-file: schema + round-trip via csv.DictReader
# ---------------------------------------------------------------------------


def test_csv_has_required_columns(writer: SmsChatExportWriter, sample_thread: SmsThread) -> None:
    raw = writer.write_csv(sample_thread)
    assert isinstance(raw, bytes)
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8")))
    assert reader.fieldnames == ["thread_id", "sender", "recipient", "timestamp_iso", "body"]


def test_csv_row_count_matches_messages(
    writer: SmsChatExportWriter, sample_thread: SmsThread
) -> None:
    raw = writer.write_csv(sample_thread)
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))
    assert len(rows) == len(sample_thread.messages)


def test_csv_thread_id_consistent(writer: SmsChatExportWriter, sample_thread: SmsThread) -> None:
    raw = writer.write_csv(sample_thread)
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))
    assert all(r["thread_id"] == "thread-001" for r in rows)


def test_csv_body_text_preserved(writer: SmsChatExportWriter, sample_thread: SmsThread) -> None:
    raw = writer.write_csv(sample_thread)
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))
    bodies = [r["body"] for r in rows]
    expected = [m.body for m in sample_thread.messages]
    assert bodies == expected


def test_csv_timestamps_monotone(writer: SmsChatExportWriter, sample_thread: SmsThread) -> None:
    raw = writer.write_csv(sample_thread)
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))
    timestamps = [r["timestamp_iso"] for r in rows]
    assert timestamps == sorted(timestamps), "Timestamps must be non-decreasing"


def test_csv_single_message_thread(writer: SmsChatExportWriter) -> None:
    thread = SmsThread(
        thread_id="single-msg",
        messages=[SmsMessage("alice", "bob", "2024-01-15T12:00:00+00:00", "Hello!")],
    )
    raw = writer.write_csv(thread)
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))
    assert len(rows) == 1
    assert rows[0]["body"] == "Hello!"


# ---------------------------------------------------------------------------
# SQLite golden-file: schema + round-trip via sqlite3
# ---------------------------------------------------------------------------


def test_sqlite_has_messages_table(writer: SmsChatExportWriter, sample_thread: SmsThread) -> None:
    raw = writer.write_sqlite(sample_thread)
    assert isinstance(raw, bytes)
    assert len(raw) > 0
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        with open(tmp, "wb") as f:
            f.write(raw)
        conn = sqlite3.connect(tmp)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "messages" in tables
        assert "threads" in tables
        conn.close()
    finally:
        os.unlink(tmp)


def test_sqlite_messages_schema(writer: SmsChatExportWriter, sample_thread: SmsThread) -> None:
    raw = writer.write_sqlite(sample_thread)
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        with open(tmp, "wb") as f:
            f.write(raw)
        conn = sqlite3.connect(tmp)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
        required = {"id", "thread_id", "sender", "recipient", "timestamp_iso", "body"}
        assert required.issubset(cols)
        conn.close()
    finally:
        os.unlink(tmp)


def test_sqlite_row_count_matches_messages(
    writer: SmsChatExportWriter, sample_thread: SmsThread
) -> None:
    raw = writer.write_sqlite(sample_thread)
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        with open(tmp, "wb") as f:
            f.write(raw)
        conn = sqlite3.connect(tmp)
        (count,) = conn.execute("SELECT COUNT(*) FROM messages").fetchone()
        assert count == len(sample_thread.messages)
        conn.close()
    finally:
        os.unlink(tmp)


def test_sqlite_threads_table_populated(
    writer: SmsChatExportWriter, sample_thread: SmsThread
) -> None:
    raw = writer.write_sqlite(sample_thread)
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        with open(tmp, "wb") as f:
            f.write(raw)
        conn = sqlite3.connect(tmp)
        row = conn.execute(
            "SELECT id, participant_count FROM threads WHERE id = ?", (sample_thread.thread_id,)
        ).fetchone()
        assert row is not None
        assert row[0] == sample_thread.thread_id
        assert row[1] == len(sample_thread.participants())
        conn.close()
    finally:
        os.unlink(tmp)


def test_sqlite_timestamps_monotone(writer: SmsChatExportWriter, sample_thread: SmsThread) -> None:
    raw = writer.write_sqlite(sample_thread)
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        with open(tmp, "wb") as f:
            f.write(raw)
        conn = sqlite3.connect(tmp)
        timestamps = [
            r[0] for r in conn.execute("SELECT timestamp_iso FROM messages ORDER BY id").fetchall()
        ]
        assert timestamps == sorted(timestamps)
        conn.close()
    finally:
        os.unlink(tmp)


# ---------------------------------------------------------------------------
# ProvenanceCatalog dispatch (CSV path via metadata)
# ---------------------------------------------------------------------------


def test_catalog_writes_sms_chat_export_csv(catalog: ProvenanceCatalog) -> None:
    raw = catalog.write(
        ArtifactProfile.SMS_CHAT_EXPORT,
        "Hi there!\nHey, what's up?",
        {
            "thread_id": "cat-thread-01",
            "sender": "alice",
            "recipient": "bob",
            "base_timestamp_iso": "2024-01-15T10:00:00+00:00",
        },
    )
    assert isinstance(raw, bytes)
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))
    assert len(rows) >= 1
    assert all(r["thread_id"] == "cat-thread-01" for r in rows)


def test_catalog_writes_sms_chat_export_sqlite(catalog: ProvenanceCatalog) -> None:
    raw = catalog.write(
        ArtifactProfile.SMS_CHAT_EXPORT,
        "SQLite test message.",
        {
            "thread_id": "cat-sqlite-01",
            "sender": "alice",
            "recipient": "bob",
            "base_timestamp_iso": "2024-01-15T10:00:00+00:00",
            "format": "sqlite",
        },
    )
    assert isinstance(raw, bytes)
    assert raw[:16] == b"SQLite format 3\x00"


def test_catalog_fallback_uses_from_to_keys(catalog: ProvenanceCatalog) -> None:
    """Generator injects 'from'/'to' and 'timestamp'; catalog should handle that."""
    raw = catalog.write(
        ArtifactProfile.SMS_CHAT_EXPORT,
        "A noise message from the generator.",
        {
            "from": "alice@example.com",
            "to": "bob@example.com",
            "timestamp": "2024-02-01 08:00:00",
        },
    )
    assert isinstance(raw, bytes)
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))
    assert len(rows) >= 1
    assert rows[0]["sender"] == "alice@example.com"


# ---------------------------------------------------------------------------
# parse_thread_from_text: monotone timestamps + ≥1 message
# ---------------------------------------------------------------------------


def test_parse_thread_monotone() -> None:
    base = datetime(2024, 1, 15, 9, 0, 0, tzinfo=UTC)
    rng = random.Random(42)
    thread = parse_thread_from_text("Line one\nLine two\nLine three", "alice", "bob", base, rng=rng)
    assert len(thread.messages) == 3
    assert thread.is_monotone()


def test_parse_thread_at_least_one_message() -> None:
    base = datetime(2024, 1, 15, 9, 0, 0, tzinfo=UTC)
    thread = parse_thread_from_text("Single message.", "alice", "bob", base)
    assert len(thread.messages) >= 1


def test_parse_thread_alternates_sender_recipient() -> None:
    base = datetime(2024, 1, 15, 9, 0, 0, tzinfo=UTC)
    thread = parse_thread_from_text("A\nB\nC\nD", "alice", "bob", base)
    assert thread.messages[0].sender == "alice"
    assert thread.messages[1].sender == "bob"
    assert thread.messages[2].sender == "alice"
    assert thread.messages[3].sender == "bob"


def test_parse_thread_empty_lines_skipped() -> None:
    base = datetime(2024, 1, 15, 9, 0, 0, tzinfo=UTC)
    thread = parse_thread_from_text("\n  \nHello\n\n", "alice", "bob", base)
    assert len(thread.messages) == 1


# ---------------------------------------------------------------------------
# SmsThread helpers
# ---------------------------------------------------------------------------


def test_sms_thread_is_monotone_true(sample_thread: SmsThread) -> None:
    assert sample_thread.is_monotone()


def test_sms_thread_is_monotone_false() -> None:
    thread = SmsThread(
        thread_id="bad",
        messages=[
            SmsMessage("a", "b", "2024-01-15T12:05:00+00:00", "later"),
            SmsMessage("b", "a", "2024-01-15T12:00:00+00:00", "earlier"),
        ],
    )
    assert not thread.is_monotone()


def test_sms_thread_participants(sample_thread: SmsThread) -> None:
    assert sample_thread.participants() == {"alice", "bob"}


# ---------------------------------------------------------------------------
# SmsChatExportEmitter: multi-participant copies
# ---------------------------------------------------------------------------


def test_emitter_produces_one_artifact_per_participant(
    two_persona_registry: PersonaRegistry, sample_thread: SmsThread
) -> None:
    emitter = SmsChatExportEmitter(
        SmsChatExportWriter(), two_persona_registry, rng=random.Random(0)
    )
    base = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
    artifacts = emitter.emit_thread(sample_thread, base)
    assert len(artifacts) == 2
    owners = {a.owner for a in artifacts}
    assert owners == {"alice", "bob"}


def test_emitter_artifacts_have_sms_chat_export_profile(
    two_persona_registry: PersonaRegistry, sample_thread: SmsThread
) -> None:
    emitter = SmsChatExportEmitter(
        SmsChatExportWriter(), two_persona_registry, rng=random.Random(0)
    )
    base = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
    artifacts = emitter.emit_thread(sample_thread, base)
    for artifact in artifacts:
        assert artifact.profile == ArtifactProfile.SMS_CHAT_EXPORT


def test_emitter_body_text_consistent_across_copies(
    two_persona_registry: PersonaRegistry, sample_thread: SmsThread
) -> None:
    emitter = SmsChatExportEmitter(
        SmsChatExportWriter(), two_persona_registry, rng=random.Random(0)
    )
    base = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
    artifacts = emitter.emit_thread(sample_thread, base)
    assert len(artifacts) == 2

    def _bodies(artifact):  # type: ignore[no-untyped-def]
        rows = list(csv.DictReader(io.StringIO(artifact.content.decode("utf-8"))))
        return [r["body"] for r in rows]

    bodies_a = _bodies(artifacts[0])
    bodies_b = _bodies(artifacts[1])
    assert bodies_a == bodies_b, "Body text must be identical across participant copies"


def test_emitter_recipient_copy_has_later_received_at(
    two_persona_registry: PersonaRegistry,
) -> None:
    """Bob's copy of Alice's first message must have a timestamp ≥ Alice's send time."""
    base = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
    thread = SmsThread(
        thread_id="recv-delay-test",
        messages=[
            SmsMessage("alice", "bob", base.isoformat(), "Hey Bob"),
        ],
    )
    emitter = SmsChatExportEmitter(
        SmsChatExportWriter(), two_persona_registry, rng=random.Random(0)
    )
    artifacts = emitter.emit_thread(thread, base)
    by_owner = {a.owner: a for a in artifacts}
    assert "alice" in by_owner
    assert "bob" in by_owner

    def _first_ts(artifact):  # type: ignore[no-untyped-def]
        rows = list(csv.DictReader(io.StringIO(artifact.content.decode("utf-8"))))
        return rows[0]["timestamp_iso"]

    alice_ts = _first_ts(by_owner["alice"])
    bob_ts = _first_ts(by_owner["bob"])
    # Bob receives the message after Alice sends it
    assert bob_ts > alice_ts, f"Expected bob_ts > alice_ts, got {bob_ts!r} vs {alice_ts!r}"


def test_emitter_sender_copy_timestamp_unchanged(
    two_persona_registry: PersonaRegistry,
) -> None:
    """Alice's own messages should not have receive delay applied."""
    base = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
    thread = SmsThread(
        thread_id="sender-ts-test",
        messages=[
            SmsMessage("alice", "bob", base.isoformat(), "Sent by Alice"),
        ],
    )
    emitter = SmsChatExportEmitter(
        SmsChatExportWriter(), two_persona_registry, rng=random.Random(0)
    )
    artifacts = emitter.emit_thread(thread, base)
    by_owner = {a.owner: a for a in artifacts}

    rows = list(csv.DictReader(io.StringIO(by_owner["alice"].content.decode("utf-8"))))
    assert rows[0]["timestamp_iso"] == base.isoformat()


def test_emitter_skips_participants_without_sms_device() -> None:
    """Participants with no SMS_CHAT_EXPORT device are silently skipped."""
    registry = PersonaRegistry(
        [
            Persona(
                name="laptop_only",
                devices=[
                    Device(
                        device_id="laptop_only_laptop",
                        owner="laptop_only",
                        permitted_profiles={ArtifactProfile.EMAIL, ArtifactProfile.PDF},
                    )
                ],
            ),
            Persona(
                name="phone_user",
                devices=[
                    Device(
                        device_id="phone_user_phone",
                        owner="phone_user",
                        permitted_profiles={ArtifactProfile.SMS_CHAT_EXPORT},
                    )
                ],
            ),
        ]
    )
    base = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
    thread = SmsThread(
        thread_id="skip-test",
        messages=[
            SmsMessage("laptop_only", "phone_user", base.isoformat(), "Can you receive this?"),
        ],
    )
    emitter = SmsChatExportEmitter(SmsChatExportWriter(), registry)
    artifacts = emitter.emit_thread(thread, base)
    owners = {a.owner for a in artifacts}
    assert "laptop_only" not in owners
    assert "phone_user" in owners


def test_emitter_thread_id_consistent_across_copies(
    two_persona_registry: PersonaRegistry, sample_thread: SmsThread
) -> None:
    emitter = SmsChatExportEmitter(
        SmsChatExportWriter(), two_persona_registry, rng=random.Random(0)
    )
    base = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
    artifacts = emitter.emit_thread(sample_thread, base)
    for artifact in artifacts:
        rows = list(csv.DictReader(io.StringIO(artifact.content.decode("utf-8"))))
        assert all(r["thread_id"] == "thread-001" for r in rows)


# ---------------------------------------------------------------------------
# Smoke: catalog via ProvenanceCatalog still produces .eml and .pdf too
# ---------------------------------------------------------------------------


def test_catalog_still_writes_email(catalog: ProvenanceCatalog) -> None:
    raw = catalog.write(
        ArtifactProfile.EMAIL,
        "Budget summary attached.",
        {
            "from": "alice@corp.com",
            "to": "bob@corp.com",
            "subject": "Q1 Budget",
            "message_id": "<smoke@evidence-factory.local>",
            "date": "Mon, 15 Jan 2024 09:00:00 +0000",
        },
    )
    assert raw.startswith(b"From: ")


def test_catalog_still_writes_pdf(catalog: ProvenanceCatalog) -> None:
    raw = catalog.write(
        ArtifactProfile.PDF,
        "Confidential report.",
        {"author": "Alice", "created": "D:20240115000000"},
    )
    assert raw.startswith(b"%PDF-")
