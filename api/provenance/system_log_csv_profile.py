"""System log / CSV profile — access_log and cdr variants.

Each emission is a single CSV file. Two schema variants:

  access_log: timestamp, controller_id, badge_id, persona_id, door_id, granted
  cdr:        call_id, start_time, end_time, calling_persona_id,
              called_persona_id, calling_number, called_number, direction

Rows within a single emission are validated monotone in time and must fall
inside the bounding event's [window_start, window_end]. The disclaimer is
captured at the manifest / SOLUTION level; CSV format has no metadata header,
so we do not embed it in the payload (keeping csv.DictReader parsing clean).
"""

from __future__ import annotations

import csv
import hashlib
import io
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

_ACCESS_LOG_FIELDS = [
    "timestamp",
    "controller_id",
    "badge_id",
    "persona_id",
    "door_id",
    "granted",
]
_CDR_FIELDS = [
    "call_id",
    "start_time",
    "end_time",
    "calling_persona_id",
    "called_persona_id",
    "calling_number",
    "called_number",
    "direction",
]


@dataclass(frozen=True)
class AccessLogRow:
    timestamp: datetime
    controller_id: str
    badge_id: str
    persona_id: str
    door_id: str
    granted: bool


@dataclass(frozen=True)
class AccessLogBrief:
    system_id: str
    rows: tuple[AccessLogRow, ...]
    window_start: datetime
    window_end: datetime


@dataclass(frozen=True)
class CdrRow:
    call_id: str
    start_time: datetime
    end_time: datetime
    calling_persona_id: str
    called_persona_id: str
    calling_number: str
    called_number: str
    direction: Literal["inbound", "outbound"]


@dataclass(frozen=True)
class CdrBrief:
    system_id: str
    rows: tuple[CdrRow, ...]
    window_start: datetime
    window_end: datetime


@dataclass(frozen=True)
class WrittenLogCsv:
    filename: str
    payload: bytes
    sha256: str
    variant: Literal["access_log", "cdr"]


def _ts(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _require_aware(*dts: datetime) -> None:
    for dt in dts:
        if dt.tzinfo is None:
            raise ValueError("all timestamps must be timezone-aware")


def _validate_access_rows(
    rows: tuple[AccessLogRow, ...],
    window_start: datetime,
    window_end: datetime,
) -> None:
    if not rows:
        raise ValueError("access_log brief must contain at least one row")
    prev: datetime | None = None
    for row in rows:
        _require_aware(row.timestamp)
        if row.timestamp < window_start or row.timestamp > window_end:
            raise ValueError(
                f"access_log row timestamp {row.timestamp} falls outside "
                f"window [{window_start}, {window_end}]"
            )
        if prev is not None and row.timestamp < prev:
            raise ValueError("access_log rows are not monotone in timestamp")
        prev = row.timestamp


def _validate_cdr_rows(
    rows: tuple[CdrRow, ...],
    window_start: datetime,
    window_end: datetime,
) -> None:
    if not rows:
        raise ValueError("cdr brief must contain at least one row")
    prev: datetime | None = None
    for row in rows:
        _require_aware(row.start_time, row.end_time)
        if row.start_time < window_start or row.end_time > window_end:
            raise ValueError(
                f"CDR row [{row.start_time}, {row.end_time}] falls outside "
                f"window [{window_start}, {window_end}]"
            )
        if prev is not None and row.start_time < prev:
            raise ValueError("cdr rows are not monotone in start_time")
        prev = row.start_time


def write_access_log(brief: AccessLogBrief, *, disclaimer: str) -> WrittenLogCsv:  # noqa: ARG001
    _require_aware(brief.window_start, brief.window_end)
    _validate_access_rows(brief.rows, brief.window_start, brief.window_end)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_ACCESS_LOG_FIELDS, lineterminator="\n")
    writer.writeheader()
    for row in brief.rows:
        writer.writerow(
            {
                "timestamp": _ts(row.timestamp),
                "controller_id": row.controller_id,
                "badge_id": row.badge_id,
                "persona_id": row.persona_id,
                "door_id": row.door_id,
                "granted": "true" if row.granted else "false",
            }
        )

    payload = buf.getvalue().encode("utf-8")
    sha = hashlib.sha256(payload).hexdigest()
    ts = brief.window_start.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = secrets.token_hex(3)
    filename = f"{ts}_access_log_{brief.system_id}_{suffix}.csv"
    return WrittenLogCsv(filename=filename, payload=payload, sha256=sha, variant="access_log")


def write_cdr(brief: CdrBrief, *, disclaimer: str) -> WrittenLogCsv:  # noqa: ARG001
    _require_aware(brief.window_start, brief.window_end)
    _validate_cdr_rows(brief.rows, brief.window_start, brief.window_end)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CDR_FIELDS, lineterminator="\n")
    writer.writeheader()
    for row in brief.rows:
        writer.writerow(
            {
                "call_id": row.call_id,
                "start_time": _ts(row.start_time),
                "end_time": _ts(row.end_time),
                "calling_persona_id": row.calling_persona_id,
                "called_persona_id": row.called_persona_id,
                "calling_number": row.calling_number,
                "called_number": row.called_number,
                "direction": row.direction,
            }
        )

    payload = buf.getvalue().encode("utf-8")
    sha = hashlib.sha256(payload).hexdigest()
    ts = brief.window_start.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = secrets.token_hex(3)
    filename = f"{ts}_cdr_{brief.system_id}_{suffix}.csv"
    return WrittenLogCsv(filename=filename, payload=payload, sha256=sha, variant="cdr")
