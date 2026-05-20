"""XLSX ledger profile.

Uses openpyxl to produce structurally valid .xlsx workbooks with round-trippable
document metadata. ZIP entries are re-ordered and stamped to epoch (1980-01-01)
so SHA-256 is stable across identical inputs.

Note: openpyxl 3.x unconditionally overwrites workbook.properties.modified on
save() with datetime.now(); we fix this by patching docProps/core.xml in the
raw ZIP after saving, before the SHA-256 is computed.
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

import openpyxl

# Excel sheet title forbids these characters and limits to 31 chars
_TITLE_INVALID = re.compile(r"[/\\?*\[\]:]")
_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")


@dataclass(frozen=True)
class XlsxBrief:
    """Content brief handed to the xlsx ledger profile writer."""

    creator_name: str
    created: datetime
    modified: datetime
    last_modified_by: str
    sheet_name: str
    rows: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class WrittenXlsx:
    filename: str
    payload: bytes
    sha256: str


def _slug(s: str, max_len: int = 40) -> str:
    s = _SAFE.sub("-", s).strip("-")
    return (s[:max_len] or "ledger").lower()


def _safe_sheet_title(name: str) -> str:
    """Sanitise a sheet name for Excel's 31-char / no-special-chars constraint."""
    safe = _TITLE_INVALID.sub("-", name)
    return (safe[:31] or "Sheet").strip()


def _patch_core_xml(core_xml: bytes, modified_iso: str) -> bytes:
    """Replace the dcterms:modified value so it survives openpyxl's save() override."""
    return re.sub(
        rb"<dcterms:modified[^>]*>[^<]*</dcterms:modified>",
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{modified_iso}</dcterms:modified>'.encode(),
        core_xml,
    )


def _normalize_zip(data: bytes, modified_iso: str) -> bytes:
    """Re-write a ZIP with: epoch timestamps, sorted entries, patched modified date."""
    src = io.BytesIO(data)
    dst = io.BytesIO()
    with zipfile.ZipFile(src, "r") as zin:
        with zipfile.ZipFile(dst, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for info in sorted(zin.infolist(), key=lambda x: x.filename):
                content = zin.read(info.filename)
                if info.filename == "docProps/core.xml":
                    content = _patch_core_xml(content, modified_iso)
                info.date_time = (1980, 1, 1, 0, 0, 0)
                zout.writestr(info, content)
    return dst.getvalue()


def _coerce_cell(value: Any) -> Any:
    """Return value with proper Python type for correct Excel cell-type inference."""
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, datetime):
        # openpyxl needs naive datetime for Excel date cells
        return value.astimezone(UTC).replace(tzinfo=None)
    return value  # str, None, etc.


def write_xlsx_ledger(brief: XlsxBrief, *, disclaimer: str) -> WrittenXlsx:
    """Serialise an XlsxBrief to .xlsx bytes.

    disclaimer is the synthetic-evidence disclaimer the PRD requires stamped
    into every artifact; stored as the workbook description property.
    """
    if brief.created.tzinfo is None:
        raise ValueError("created must be timezone-aware")
    if brief.modified < brief.created:
        raise ValueError("modified must be >= created")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = _safe_sheet_title(brief.sheet_name)

    # Core document metadata
    wb.properties.creator = brief.creator_name
    wb.properties.lastModifiedBy = brief.last_modified_by
    # openpyxl serialises these as naive UTC; strip tzinfo here
    wb.properties.created = brief.created.astimezone(UTC).replace(tzinfo=None)
    # wb.properties.modified will be overwritten by openpyxl's save(); we fix
    # it in _normalize_zip → _patch_core_xml after saving.
    wb.properties.description = disclaimer

    if brief.rows:
        headers = list(brief.rows[0].keys())
        ws.append(headers)
        for row in brief.rows:
            ws.append([_coerce_cell(row.get(h)) for h in headers])

    buf = io.BytesIO()
    wb.save(buf)

    # Patch modified date and normalise ZIP for SHA-256 stability
    modified_iso = brief.modified.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = _normalize_zip(buf.getvalue(), modified_iso)
    sha = hashlib.sha256(payload).hexdigest()

    suffix = secrets.token_hex(3)
    ts = brief.created.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    filename = f"{ts}_{_slug(brief.sheet_name)}_{suffix}.xlsx"

    return WrittenXlsx(filename=filename, payload=payload, sha256=sha)
