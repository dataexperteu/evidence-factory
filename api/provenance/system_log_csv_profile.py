"""System log / CSV profile — access logs and call-detail records.

Slice 7 adds two CSV schema variants produced by system-owned devices:

  access_log  — building access controller exports
  cdr         — PBX / telco call-detail records

Both variants guarantee:
  - Timestamp columns are ISO-8601
  - Rows are monotone in time (sorted on write)
  - Payload SHA-256 is stable for identical inputs (no random content)
  - Each row references a persona_id present in the Persona & Device Registry
"""

from __future__ import annotations

import csv
import hashlib
import io
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal


@dataclass(frozen=True)
class SystemLogBrief:
    """Content brief for the system log CSV writer."""

    schema: Literal["access_log", "cdr"]
    device_id: str
    event_id: str
    event_timestamp: datetime
    persona_ids: tuple[str, ...]  # all persona IDs from the registry (for row subjects)


@dataclass(frozen=True)
class WrittenSystemLog:
    filename: str
    payload: bytes
    sha256: str


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _phone(persona_id: str) -> str:
    """Deterministic phone number derived from persona_id (no randomness)."""
    h = int.from_bytes(persona_id.encode()[:4].ljust(4, b"\x00"), "big") % 9000 + 1000
    return f"+1-555-{h:04d}"


def _write_access_log(brief: SystemLogBrief) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["timestamp", "controller_id", "badge_id", "persona_id", "door_id", "granted"])
    door_ids = ["door-main", "door-lab", "door-server"]
    controller_id = f"ctrl-{brief.device_id}"
    # Sorted personas for determinism; rows monotone at 5-min intervals
    for i, persona_id in enumerate(sorted(brief.persona_ids)):
        ts = brief.event_timestamp + timedelta(minutes=i * 5)
        writer.writerow(
            [
                _iso(ts),
                controller_id,
                f"badge-{persona_id}",
                persona_id,
                door_ids[i % len(door_ids)],
                "true",
            ]
        )
    return buf.getvalue().encode("utf-8")


def _write_cdr(brief: SystemLogBrief) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "call_id",
            "start_time",
            "end_time",
            "calling_persona_id",
            "called_persona_id",
            "calling_number",
            "called_number",
            "direction",
        ]
    )
    sorted_personas = sorted(brief.persona_ids)
    n = len(sorted_personas)
    if n < 2:
        return buf.getvalue().encode("utf-8")
    # Produce one call record per adjacent pair, timestamps monotone at 10-min intervals
    for i in range(min(n - 1, 3)):
        caller = sorted_personas[i]
        callee = sorted_personas[i + 1]
        start = brief.event_timestamp + timedelta(minutes=i * 10)
        end = start + timedelta(minutes=5)
        writer.writerow(
            [
                f"call-{brief.event_id}-{i:03d}",
                _iso(start),
                _iso(end),
                caller,
                callee,
                _phone(caller),
                _phone(callee),
                "outbound",
            ]
        )
    return buf.getvalue().encode("utf-8")


def write_system_log_csv(brief: SystemLogBrief, *, disclaimer: str) -> WrittenSystemLog:
    """Serialise a SystemLogBrief to CSV bytes.

    ``disclaimer`` is attached via a leading comment row so the file carries
    the synthetic-evidence notice alongside the provenance manifest entry.
    """
    if brief.event_timestamp.tzinfo is None:
        raise ValueError("event_timestamp must be timezone-aware")
    if not brief.persona_ids:
        raise ValueError("system log must reference at least one persona_id")

    if brief.schema == "cdr":
        body = _write_cdr(brief)
    else:
        body = _write_access_log(brief)

    # Leading comment row carries the synthetic-evidence watermark. Forensic CSV
    # readers skip `#`-prefixed lines, so this does not disturb the columns.
    comment = f"# SYNTHETIC EVIDENCE — {disclaimer}\n".encode()
    payload = comment + body

    sha = hashlib.sha256(payload).hexdigest()

    ts = brief.event_timestamp.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    # Filename uses a random suffix so two events at the same second don't collide
    suffix = secrets.token_hex(3)
    filename = f"{ts}_{brief.schema}_{brief.event_id}_{suffix}.csv"
    return WrittenSystemLog(filename=filename, payload=payload, sha256=sha)
