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
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

# openpyxl always writes dcterms:modified as datetime.now() during save(),
# so we replace the modified timestamp in core.xml with a deterministic one.
_MODIFIED_RE = re.compile(rb"(<dcterms:modified[^>]*>)[^<]*(</dcterms:modified>)")


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


_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)  # minimum valid zip timestamp


def _deterministic_bytes(wb: Any) -> bytes:
    """Save workbook to bytes with all wall-clock timestamps replaced.

    openpyxl (a) embeds the current wall-clock second in every zip entry's
    local-file header and (b) overwrites dcterms:modified in core.xml with
    datetime.now() on every save().  We strip both sources of non-determinism
    so SHA-256 is stable for identical workbook state.
    """
    # Capture the intended modified time before save() overwrites it.
    intended_modified: datetime | None = getattr(wb.properties, "modified", None)

    raw = io.BytesIO()
    wb.save(raw)

    # Re-encode the intended modified timestamp as an ISO-8601 UTC string.
    if intended_modified is not None:
        if intended_modified.tzinfo is None:
            fixed_ts = intended_modified.strftime("%Y-%m-%dT%H:%M:%SZ").encode()
        else:
            fixed_ts = intended_modified.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ").encode()
    else:
        fixed_ts = b"1970-01-01T00:00:00Z"

    out = io.BytesIO()
    with zipfile.ZipFile(raw, "r") as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for entry in sorted(zin.infolist(), key=lambda e: e.filename):
            data = zin.read(entry.filename)
            if entry.filename == "docProps/core.xml":
                data = _MODIFIED_RE.sub(rb"\g<1>" + fixed_ts + rb"\g<2>", data)
            info = zipfile.ZipInfo(entry.filename, date_time=_ZIP_EPOCH)
            info.compress_type = entry.compress_type
            zout.writestr(info, data)
    return out.getvalue()
