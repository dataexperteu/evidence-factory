"""SMS/chat export profile — CSV and SQLite formats.

Schema
------
CSV columns : thread_id, sender, recipient, timestamp_iso, body
              timestamp_iso reflects the owner's device perspective:
              sent_iso when the owner is the sender, sent_iso + DELIVERY_DELTA when received.

SQLite tables
  threads  : thread_id TEXT PRIMARY KEY, owner TEXT NOT NULL, disclaimer TEXT NOT NULL
  messages : id INTEGER PK AUTOINCREMENT, thread_id TEXT, sender TEXT, recipient TEXT,
             sent_iso TEXT, received_iso TEXT, body TEXT

One emission = one conversation thread held by one participant (the `owner`).
For multi-participant corroboration, callers invoke the writer once per participant;
each copy carries adjusted received_iso / timestamp_iso timestamps.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

DELIVERY_DELTA = timedelta(seconds=2)

_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")


def _slug(s: str, max_len: int = 30) -> str:
    return (_SAFE.sub("-", s).strip("-")[:max_len] or "unknown").lower()


@dataclass(frozen=True)
class SmsMessage:
    """A single message within a conversation thread."""

    sender: str      # display name / persona label
    recipient: str   # display name / persona label
    timestamp_iso: str  # ISO 8601 tz-aware sent timestamp; must be monotone within thread
    body: str


@dataclass(frozen=True)
class SmsBrief:
    """Content brief for one conversation thread.

    `participants` names everyone in the thread (used for multi-copy validation).
    `messages` must be non-empty with monotone timestamps inside [window_start, window_end].
    """

    thread_id: str
    participants: tuple[str, ...]
    messages: tuple[SmsMessage, ...]
    window_start: datetime  # bounding event window — tz-aware
    window_end: datetime    # bounding event window — tz-aware


@dataclass(frozen=True)
class WrittenSms:
    filename: str
    payload: bytes
    sha256: str
    format: Literal["csv", "sqlite"]
    thread_id: str
    owner: str


def _validate(brief: SmsBrief) -> None:
    if not brief.messages:
        raise ValueError("thread must contain at least one message")
    if brief.window_start.tzinfo is None or brief.window_end.tzinfo is None:
        raise ValueError("window timestamps must be timezone-aware")
    if brief.window_start > brief.window_end:
        raise ValueError("window_start must be <= window_end")

    prev: datetime | None = None
    for msg in brief.messages:
        ts = datetime.fromisoformat(msg.timestamp_iso)
        if ts.tzinfo is None:
            raise ValueError(f"message timestamp must be timezone-aware: {msg.timestamp_iso}")
        if not (brief.window_start <= ts <= brief.window_end):
            raise ValueError(
                f"timestamp {msg.timestamp_iso} outside event window "
                f"[{brief.window_start.isoformat()}, {brief.window_end.isoformat()}]"
            )
        if prev is not None and ts < prev:
            raise ValueError(
                f"timestamps must be monotonically non-decreasing: "
                f"{prev.isoformat()} > {ts.isoformat()}"
            )
        prev = ts


def _perspective_iso(msg: SmsMessage, owner: str) -> str:
    """Owner-perspective delivery timestamp.

    Sent messages keep the original timestamp.  Received messages get
    sent_iso + DELIVERY_DELTA to simulate realistic delivery lag.
    """
    if msg.sender == owner:
        return msg.timestamp_iso
    sent = datetime.fromisoformat(msg.timestamp_iso)
    return (sent + DELIVERY_DELTA).isoformat()


def write_sms_csv(brief: SmsBrief, *, owner: str, disclaimer: str) -> WrittenSms:  # noqa: ARG001
    """Serialise a conversation thread to CSV bytes from `owner`'s perspective."""
    _validate(brief)

    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=["thread_id", "sender", "recipient", "timestamp_iso", "body"],
        lineterminator="\n",
    )
    writer.writeheader()
    for msg in brief.messages:
        writer.writerow(
            {
                "thread_id": brief.thread_id,
                "sender": msg.sender,
                "recipient": msg.recipient,
                "timestamp_iso": _perspective_iso(msg, owner),
                "body": msg.body,
            }
        )

    payload = buf.getvalue().encode("utf-8")
    sha = hashlib.sha256(payload).hexdigest()
    suffix = secrets.token_hex(3)
    ts_str = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    filename = f"sms_{_slug(owner)}_{ts_str}_{suffix}.csv"
    return WrittenSms(
        filename=filename,
        payload=payload,
        sha256=sha,
        format="csv",
        thread_id=brief.thread_id,
        owner=owner,
    )


def write_sms_sqlite(brief: SmsBrief, *, owner: str, disclaimer: str) -> WrittenSms:
    """Serialise a conversation thread to a SQLite database from `owner`'s perspective.

    Uses connection.serialize() (Python 3.11+) on an in-memory database to
    produce a portable .db byte payload without touching the filesystem.
    """
    _validate(brief)

    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "CREATE TABLE threads ("
        "thread_id TEXT PRIMARY KEY, "
        "owner TEXT NOT NULL, "
        "disclaimer TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE messages ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "thread_id TEXT NOT NULL, "
        "sender TEXT NOT NULL, "
        "recipient TEXT NOT NULL, "
        "sent_iso TEXT NOT NULL, "
        "received_iso TEXT NOT NULL, "
        "body TEXT NOT NULL, "
        "FOREIGN KEY (thread_id) REFERENCES threads(thread_id))"
    )
    conn.execute(
        "INSERT INTO threads VALUES (?, ?, ?)",
        (brief.thread_id, owner, disclaimer),
    )
    for msg in brief.messages:
        received = _perspective_iso(msg, owner)
        conn.execute(
            "INSERT INTO messages "
            "(thread_id, sender, recipient, sent_iso, received_iso, body) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (brief.thread_id, msg.sender, msg.recipient, msg.timestamp_iso, received, msg.body),
        )
    conn.commit()

    payload: bytes = conn.serialize()  # type: ignore[attr-defined]  # py311+
    conn.close()

    sha = hashlib.sha256(payload).hexdigest()
    suffix = secrets.token_hex(3)
    ts_str = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    filename = f"sms_{_slug(owner)}_{ts_str}_{suffix}.db"
    return WrittenSms(
        filename=filename,
        payload=payload,
        sha256=sha,
        format="sqlite",
        thread_id=brief.thread_id,
        owner=owner,
    )
