"""SMS / chat export profile: ArtifactWriter, SmsChatExportWriter, SmsChatExportEmitter."""

from __future__ import annotations

import csv
import io
import os
import random
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..models import Artifact
    from ..registry import PersonaRegistry


@runtime_checkable
class ArtifactWriter(Protocol):
    """Interface for all Provenance Catalog profile writers."""

    def write(self, text_content: str, metadata: dict[str, Any]) -> bytes: ...


@dataclass
class SmsMessage:
    sender: str
    recipient: str
    timestamp_iso: str
    body: str


@dataclass
class SmsThread:
    thread_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    messages: list[SmsMessage] = field(default_factory=list)

    def is_monotone(self) -> bool:
        """Return True when message timestamps are strictly non-decreasing."""
        for a, b in zip(self.messages, self.messages[1:], strict=False):
            if a.timestamp_iso > b.timestamp_iso:
                return False
        return True

    def participants(self) -> set[str]:
        names: set[str] = set()
        for m in self.messages:
            names.add(m.sender)
            names.add(m.recipient)
        return names


def parse_thread_from_text(
    text_content: str,
    sender: str,
    recipient: str,
    base_timestamp: datetime,
    interval_seconds: int = 30,
    rng: random.Random | None = None,
) -> SmsThread:
    """Parse newline-separated text into an SmsThread with monotone timestamps.

    Lines alternate sender→recipient, recipient→sender to simulate a real exchange.
    """
    _rng = rng or random.Random()
    thread = SmsThread()
    current_ts = base_timestamp
    lines = [ln for ln in text_content.splitlines() if ln.strip()]
    for i, line in enumerate(lines):
        msg_sender, msg_recipient = (sender, recipient) if i % 2 == 0 else (recipient, sender)
        thread.messages.append(
            SmsMessage(
                sender=msg_sender,
                recipient=msg_recipient,
                timestamp_iso=current_ts.isoformat(),
                body=line.strip(),
            )
        )
        current_ts += timedelta(seconds=interval_seconds + _rng.randint(0, 30))
    return thread


class SmsChatExportWriter:
    """Writes SMS/chat threads as CSV or SQLite.

    CSV columns: thread_id, sender, recipient, timestamp_iso, body
    SQLite schema: messages table with the same five columns plus an autoincrement id.
    """

    def write(self, text_content: str, metadata: dict[str, Any]) -> bytes:
        """Write from raw text_content + metadata dict (used by ProvenanceCatalog)."""
        thread = self._build_thread(text_content, metadata)
        fmt = str(metadata.get("format", "csv")).lower()
        return self.write_sqlite(thread) if fmt == "sqlite" else self.write_csv(thread)

    def write_csv(self, thread: SmsThread) -> bytes:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["thread_id", "sender", "recipient", "timestamp_iso", "body"])
        for msg in thread.messages:
            writer.writerow(
                [thread.thread_id, msg.sender, msg.recipient, msg.timestamp_iso, msg.body]
            )
        return buf.getvalue().encode("utf-8")

    def write_sqlite(self, thread: SmsThread) -> bytes:
        fd, tmp_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            conn = sqlite3.connect(tmp_path)
            conn.execute(
                "CREATE TABLE messages ("
                "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  thread_id TEXT NOT NULL,"
                "  sender TEXT NOT NULL,"
                "  recipient TEXT NOT NULL,"
                "  timestamp_iso TEXT NOT NULL,"
                "  body TEXT NOT NULL"
                ")"
            )
            conn.execute(
                "CREATE TABLE threads ("
                "  id TEXT PRIMARY KEY,"
                "  participant_count INTEGER NOT NULL,"
                "  first_message_ts TEXT NOT NULL,"
                "  last_message_ts TEXT NOT NULL"
                ")"
            )
            for msg in thread.messages:
                conn.execute(
                    "INSERT INTO messages (thread_id, sender, recipient, timestamp_iso, body)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (thread.thread_id, msg.sender, msg.recipient, msg.timestamp_iso, msg.body),
                )
            if thread.messages:
                first_ts = thread.messages[0].timestamp_iso
                last_ts = thread.messages[-1].timestamp_iso
                participant_count = len(thread.participants())
                sql = (
                    "INSERT OR REPLACE INTO threads"
                    " (id, participant_count, first_message_ts, last_message_ts)"
                    " VALUES (?, ?, ?, ?)"
                )
                conn.execute(sql, (thread.thread_id, participant_count, first_ts, last_ts))
            conn.commit()
            conn.close()
            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            os.unlink(tmp_path)

    def _build_thread(self, text_content: str, metadata: dict[str, Any]) -> SmsThread:
        if "messages" in metadata:
            msgs = [
                SmsMessage(
                    sender=str(m["sender"]),
                    recipient=str(m["recipient"]),
                    timestamp_iso=str(m["timestamp_iso"]),
                    body=str(m["body"]),
                )
                for m in metadata["messages"]
            ]
            return SmsThread(
                thread_id=str(metadata.get("thread_id", uuid.uuid4())),
                messages=msgs,
            )

        sender = str(metadata.get("sender", metadata.get("from", "sender")))
        recipient = str(metadata.get("recipient", metadata.get("to", "recipient")))
        thread_id = str(metadata.get("thread_id", uuid.uuid4()))
        base_ts_raw = metadata.get("base_timestamp_iso", metadata.get("timestamp", ""))
        try:
            base_ts = datetime.fromisoformat(str(base_ts_raw)) if base_ts_raw else datetime.now(UTC)
        except ValueError:
            base_ts = datetime.now(UTC)

        thread = parse_thread_from_text(text_content, sender, recipient, base_ts)
        thread.thread_id = thread_id
        return thread


class SmsChatExportEmitter:
    """Produces one Artifact per permitted participant device from a single SmsThread.

    The body text is identical across all copies; receiving-side timestamps are
    nudged forward by a realistic delivery delay (2–15 s) per recipient.
    """

    _RECEIVE_DELAY_MIN_S = 2
    _RECEIVE_DELAY_MAX_S = 15

    def __init__(
        self,
        writer: SmsChatExportWriter,
        registry: PersonaRegistry,
        rng: random.Random | None = None,
    ) -> None:
        self._writer = writer
        self._registry = registry
        self._rng = rng or random.Random()

    def emit_thread(
        self,
        thread: SmsThread,
        base_timestamp: datetime,
        fmt: str = "csv",
    ) -> list[Artifact]:
        """Return one Artifact per participant with an SMS-capable device."""
        from ..models import Artifact, ArtifactProfile

        participants = thread.participants()
        artifacts: list[Artifact] = []

        for owner in sorted(participants):
            devices = self._registry.devices_for(owner)
            sms_devices = [
                d for d in devices if ArtifactProfile.SMS_CHAT_EXPORT in d.permitted_profiles
            ]
            if not sms_devices:
                continue
            device = sms_devices[0]

            perspective_thread = self._perspective_copy(thread, owner)
            metadata: dict[str, Any] = {
                "thread_id": perspective_thread.thread_id,
                "messages": [
                    {
                        "sender": m.sender,
                        "recipient": m.recipient,
                        "timestamp_iso": m.timestamp_iso,
                        "body": m.body,
                    }
                    for m in perspective_thread.messages
                ],
                "format": fmt,
            }
            content = self._writer.write("", metadata)
            artifacts.append(
                Artifact(
                    owner=owner,
                    device=device.device_id,
                    profile=ArtifactProfile.SMS_CHAT_EXPORT,
                    timestamp=base_timestamp,
                    content=content,
                    text_content=" ".join(m.body for m in thread.messages),
                    metadata={"thread_id": thread.thread_id},
                    is_noise=False,
                )
            )

        return artifacts

    def _perspective_copy(self, thread: SmsThread, owner: str) -> SmsThread:
        """Return a copy of thread with receive-delay applied for messages received by owner."""
        adjusted: list[SmsMessage] = []
        for msg in thread.messages:
            if msg.recipient == owner and msg.sender != owner:
                try:
                    ts = datetime.fromisoformat(msg.timestamp_iso)
                    delay = self._rng.randint(self._RECEIVE_DELAY_MIN_S, self._RECEIVE_DELAY_MAX_S)
                    ts_adj = (ts + timedelta(seconds=delay)).isoformat()
                except ValueError:
                    ts_adj = msg.timestamp_iso
                adjusted.append(
                    SmsMessage(
                        sender=msg.sender,
                        recipient=msg.recipient,
                        timestamp_iso=ts_adj,
                        body=msg.body,
                    )
                )
            else:
                adjusted.append(msg)
        return SmsThread(thread_id=thread.thread_id, messages=adjusted)
