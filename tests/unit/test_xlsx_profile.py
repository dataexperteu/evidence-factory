"""Provenance Catalog — XLSX ledger profile golden round-trip (slice 5).

Every emitted .xlsx must be parseable by openpyxl.load_workbook and carry
correct document metadata. SHA-256 must be stable across two identical writes.
"""

from __future__ import annotations

import hashlib
import io
from datetime import UTC, datetime, timedelta
from typing import Any

import openpyxl
import pytest

from api.provenance.xlsx_profile import XlsxBrief, write_xlsx_ledger


def _brief(**overrides: Any) -> XlsxBrief:
    base = datetime(2024, 6, 3, 9, 15, tzinfo=UTC)
    defaults: dict[str, Any] = dict(
        creator_name="Sherlock Holmes",
        created=base,
        modified=base + timedelta(hours=1),
        last_modified_by="Sherlock Holmes",
        sheet_name="Evidence Ledger",
        rows=(
            {
                "description": "Retainer fee",
                "amount": 250.00,
                "date": datetime(2024, 6, 1, tzinfo=UTC),
            },
            {
                "description": "Expenses",
                "amount": 42.50,
                "date": datetime(2024, 6, 2, tzinfo=UTC),
            },
            {
                "description": "Travel allowance",
                "amount": 18.75,
                "date": datetime(2024, 6, 3, tzinfo=UTC),
            },
        ),
    )
    defaults.update(overrides)
    return XlsxBrief(**defaults)


def _load_ro(payload: bytes) -> openpyxl.Workbook:
    return openpyxl.load_workbook(io.BytesIO(payload), read_only=True)


# ---------------------------------------------------------------------------
# AC-1: profile parses cleanly with openpyxl.load_workbook(..., read_only=True)
# ---------------------------------------------------------------------------


def test_xlsx_parses_with_openpyxl_read_only():
    out = write_xlsx_ledger(_brief(), disclaimer="SYNTHETIC EVIDENCE — demo")
    wb = _load_ro(out.payload)
    ws = wb.active
    assert ws is not None
    wb.close()


# ---------------------------------------------------------------------------
# AC-2: workbook core metadata round-trips
# ---------------------------------------------------------------------------


def test_xlsx_metadata_round_trips():
    brief = _brief()
    out = write_xlsx_ledger(brief, disclaimer="d")
    # Use full (non-read-only) workbook to access properties
    wb = openpyxl.load_workbook(io.BytesIO(out.payload))
    assert wb.properties.creator == brief.creator_name
    assert wb.properties.lastModifiedBy == brief.last_modified_by
    # Dates are stored and returned as naive UTC in OOXML core.xml
    expected_created = brief.created.astimezone(UTC).replace(tzinfo=None)
    expected_modified = brief.modified.astimezone(UTC).replace(tzinfo=None)
    assert wb.properties.created == expected_created
    assert wb.properties.modified == expected_modified
    assert wb.properties.modified >= wb.properties.created


# ---------------------------------------------------------------------------
# AC-3a: single-sheet ledger — rows = transactions / line items
# ---------------------------------------------------------------------------


def test_xlsx_single_sheet_ledger_structure():
    out = write_xlsx_ledger(_brief(), disclaimer="d")
    wb = _load_ro(out.payload)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    # header row
    assert rows[0] == ("description", "amount", "date")
    # three data rows
    assert len(rows) == 4  # 1 header + 3 data
    assert rows[1][0] == "Retainer fee"
    assert rows[2][0] == "Expenses"
    assert rows[3][0] == "Travel allowance"


# ---------------------------------------------------------------------------
# AC-3b: correct cell-type inference for numeric columns
# ---------------------------------------------------------------------------


def test_xlsx_numeric_cell_type_inference():
    out = write_xlsx_ledger(_brief(), disclaimer="d")
    wb = _load_ro(out.payload)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    # amount column (index 1) must be numeric
    assert isinstance(rows[1][1], (int, float)), f"expected numeric, got {type(rows[1][1])}"
    assert rows[1][1] == pytest.approx(250.00)
    assert rows[2][1] == pytest.approx(42.50)
    assert rows[3][1] == pytest.approx(18.75)


# ---------------------------------------------------------------------------
# AC-3c: correct cell-type inference for date columns
# ---------------------------------------------------------------------------


def test_xlsx_date_cell_type_inference():
    out = write_xlsx_ledger(_brief(), disclaimer="d")
    # Date detection requires data_only + non-read-only in some openpyxl versions
    wb = openpyxl.load_workbook(io.BytesIO(out.payload))
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    # date column (index 2) must be a datetime object
    assert isinstance(rows[1][2], datetime), f"expected datetime, got {type(rows[1][2])}"


# ---------------------------------------------------------------------------
# AC-3d: SHA-256 stability across two writes given identical inputs
# ---------------------------------------------------------------------------


def test_sha256_matches_payload():
    out = write_xlsx_ledger(_brief(), disclaimer="d")
    assert hashlib.sha256(out.payload).hexdigest() == out.sha256


def test_sha256_stable_across_two_writes():
    brief = _brief()
    w1 = write_xlsx_ledger(brief, disclaimer="SYNTHETIC EVIDENCE — demo")
    w2 = write_xlsx_ledger(brief, disclaimer="SYNTHETIC EVIDENCE — demo")
    assert w1.sha256 == w2.sha256
    assert w1.payload == w2.payload


# ---------------------------------------------------------------------------
# Validation guards
# ---------------------------------------------------------------------------


def test_requires_timezone_aware_created():
    with pytest.raises(ValueError, match="timezone-aware"):
        write_xlsx_ledger(_brief(created=datetime(2024, 6, 3, 9, 15)), disclaimer="d")


def test_modified_must_be_ge_created():
    base = datetime(2024, 6, 3, tzinfo=UTC)
    with pytest.raises(ValueError, match="modified"):
        write_xlsx_ledger(
            _brief(created=base, modified=base - timedelta(seconds=1)), disclaimer="d"
        )


def test_modified_equal_to_created_is_valid():
    base = datetime(2024, 6, 3, tzinfo=UTC)
    out = write_xlsx_ledger(_brief(created=base, modified=base), disclaimer="d")
    assert out.sha256


def test_filename_is_filesystem_safe():
    out = write_xlsx_ledger(_brief(sheet_name="Invoice/Budget:2024*Plan"), disclaimer="d")
    assert "/" not in out.filename
    assert ":" not in out.filename
    assert "*" not in out.filename
    assert out.filename.endswith(".xlsx")


def test_filename_contains_iso_timestamp():
    brief = _brief()
    out = write_xlsx_ledger(brief, disclaimer="d")
    ts = brief.created.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    assert ts in out.filename


def test_empty_rows_still_produces_valid_workbook():
    out = write_xlsx_ledger(_brief(rows=()), disclaimer="d")
    wb = _load_ro(out.payload)
    assert wb.active is not None
    wb.close()
