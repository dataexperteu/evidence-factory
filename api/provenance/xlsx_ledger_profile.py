"""XLSX ledger profile — structured workbook with typed columns and core metadata.

Produces openpyxl workbooks whose docProps/core.xml metadata round-trips
faithfully: creator, created, modified, lastModifiedBy all derive from the
owning persona and the event timestamp so provenance is self-contained.
"""

from __future__ import annotations

import hashlib
import io
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class XlsxLedgerBrief:
    """Content brief handed to the xlsx_ledger profile writer."""

    creator_name: str
    created_at: datetime
    headers: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    sheet_title: str = "Ledger"
    last_modified_by: str = ""
    modified_at: datetime | None = None


@dataclass(frozen=True)
class WrittenXlsxLedger:
    filename: str
    payload: bytes
    sha256: str


_TITLE_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")
# openpyxl rejects these characters in worksheet titles
_SHEET_INVALID = re.compile(r"[/\\?*:\[\]]")


def _slug(s: str, max_len: int = 40) -> str:
    s = _TITLE_SAFE.sub("-", s).strip("-")
    return (s[:max_len] or "ledger").lower()


def _safe_sheet_title(s: str, max_len: int = 31) -> str:
    s = _SHEET_INVALID.sub("-", s).strip()
    return s[:max_len] or "Ledger"


def write_xlsx_ledger(brief: XlsxLedgerBrief, *, disclaimer: str) -> WrittenXlsxLedger:
    """Serialise an XlsxLedgerBrief to an .xlsx workbook byte-string.

    Core metadata is set deterministically from the brief so SHA-256 is stable
    across two writes given identical inputs.  `disclaimer` is stored in the
    workbook ``keywords`` core property (the format-appropriate watermark slot)
    and mirrored into ``description`` so the chain-of-custody manifest and the
    file both carry it.
    """
    if brief.created_at.tzinfo is None:
        raise ValueError("created_at must be timezone-aware")

    import openpyxl
    from openpyxl import Workbook

    wb: Workbook = openpyxl.Workbook()
    ws = wb.active
    if ws is None:
        ws = wb.create_sheet()
    ws.title = _safe_sheet_title(brief.sheet_title)

    creator = brief.creator_name
    last_modified_by = brief.last_modified_by or creator
    # Strip timezone for openpyxl (it stores naive datetimes in ISO 8601)
    created_naive = brief.created_at.astimezone(UTC).replace(tzinfo=None)
    modified_dt = brief.modified_at or brief.created_at
    if modified_dt < brief.created_at:
        modified_dt = brief.created_at
    modified_naive = modified_dt.astimezone(UTC).replace(tzinfo=None)

    wb.properties.creator = creator
    wb.properties.created = created_naive
    wb.properties.modified = modified_naive
    wb.properties.lastModifiedBy = last_modified_by
    wb.properties.description = disclaimer
    wb.properties.keywords = disclaimer

    if brief.headers:
        ws.append(list(brief.headers))
    for row in brief.rows:
        ws.append(list(row))

    payload = _deterministic_bytes(wb)
    sha = hashlib.sha256(payload).hexdigest()

    suffix = secrets.token_hex(3)
    ts = brief.created_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    filename = f"{ts}_{_slug(brief.sheet_title)}_{suffix}.xlsx"
    return WrittenXlsxLedger(filename=filename, payload=payload, sha256=sha)


def _deterministic_bytes(wb: Any) -> bytes:
    """Save workbook to bytes.  openpyxl is deterministic given identical
    workbook state, so calling this twice with the same wb produces the same
    bytes — SHA-256 stability is guaranteed by the caller setting all
    timestamps from metadata rather than letting openpyxl use wall-clock time.
    """
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
