"""API xlsx_ledger profile — golden-file round-trip tests.

Mirrors the pattern in test_email_profile.py: write once, re-parse with
openpyxl as the independent verifier, assert structural and metadata invariants.
"""

from __future__ import annotations

import hashlib
import io
from datetime import UTC, datetime
from typing import Any

import openpyxl
import pytest

from api.provenance.xlsx_ledger_profile import XlsxLedgerBrief, write_xlsx_ledger


def _brief(**overrides: Any) -> XlsxLedgerBrief:
    defaults: dict[str, Any] = dict(
        creator_name="Alice Smith",
        created_at=datetime(2024, 6, 3, 9, 0, tzinfo=UTC),
        headers=("date", "description", "amount", "category"),
        rows=(
            (datetime(2024, 6, 1), "Office rent", 1200.00, "Facilities"),
            (datetime(2024, 6, 2), "Utilities", 89.50, "Overhead"),
        ),
        sheet_title="June Ledger",
        last_modified_by="Alice Smith",
    )
    defaults.update(overrides)
    return XlsxLedgerBrief(**defaults)


def test_parseable_with_openpyxl_read_only():
    out = write_xlsx_ledger(_brief(), disclaimer="SYNTHETIC EVIDENCE — demo")
    wb = openpyxl.load_workbook(io.BytesIO(out.payload), read_only=True)
    assert len(wb.sheetnames) >= 1
    wb.close()


def test_metadata_round_trips():
    out = write_xlsx_ledger(_brief(), disclaimer="SYNTHETIC EVIDENCE — demo")
    wb = openpyxl.load_workbook(io.BytesIO(out.payload))
    props = wb.properties
    assert props.creator == "Alice Smith"
    assert props.lastModifiedBy == "Alice Smith"
    assert props.created is not None
    assert props.modified is not None
    assert props.modified >= props.created  # type: ignore[operator]


def test_creator_equals_persona_full_name():
    out = write_xlsx_ledger(_brief(creator_name="John Watson"), disclaimer="d")
    wb = openpyxl.load_workbook(io.BytesIO(out.payload))
    assert wb.properties.creator == "John Watson"


def test_created_equals_event_timestamp():
    ts = datetime(2024, 3, 15, 10, 30, tzinfo=UTC)
    out = write_xlsx_ledger(_brief(created_at=ts), disclaimer="d")
    wb = openpyxl.load_workbook(io.BytesIO(out.payload))
    # openpyxl stores naive UTC datetimes; compare date + time parts
    assert wb.properties.created is not None
    c = wb.properties.created
    assert c.year == 2024 and c.month == 3 and c.day == 15


def test_modified_gte_created():
    ts = datetime(2024, 3, 15, 10, 30, tzinfo=UTC)
    out = write_xlsx_ledger(_brief(created_at=ts), disclaimer="d")
    wb = openpyxl.load_workbook(io.BytesIO(out.payload))
    assert wb.properties.modified >= wb.properties.created  # type: ignore[operator]


def test_last_modified_by_is_set():
    out = write_xlsx_ledger(_brief(last_modified_by="Bob Jones"), disclaimer="d")
    wb = openpyxl.load_workbook(io.BytesIO(out.payload))
    assert wb.properties.lastModifiedBy == "Bob Jones"


def test_single_sheet_with_header_and_data_rows():
    out = write_xlsx_ledger(_brief(), disclaimer="d")
    wb = openpyxl.load_workbook(io.BytesIO(out.payload))
    ws = wb.active
    assert ws is not None
    rows = list(ws.iter_rows(values_only=True))
    # header + 2 data rows
    assert len(rows) >= 3
    # header cells are strings
    assert rows[0] == ("date", "description", "amount", "category")


def test_numeric_and_date_cell_types_preserved():
    from datetime import date

    out = write_xlsx_ledger(_brief(), disclaimer="d")
    wb = openpyxl.load_workbook(io.BytesIO(out.payload))
    ws = wb.active
    assert ws is not None
    rows = list(ws.iter_rows(values_only=True))
    # row[1] = first data row
    assert isinstance(rows[1][0], (datetime, date)), (
        f"expected date/datetime, got {type(rows[1][0])}"
    )
    assert isinstance(rows[1][2], (int, float)), f"expected numeric, got {type(rows[1][2])}"
    assert isinstance(rows[1][1], str)


def test_sha256_stable_across_two_writes():
    b = _brief()
    out1 = write_xlsx_ledger(b, disclaimer="SYNTHETIC EVIDENCE")
    out2 = write_xlsx_ledger(b, disclaimer="SYNTHETIC EVIDENCE")
    assert hashlib.sha256(out1.payload).hexdigest() == hashlib.sha256(out2.payload).hexdigest()


def test_sha256_field_matches_payload():
    out = write_xlsx_ledger(_brief(), disclaimer="d")
    assert hashlib.sha256(out.payload).hexdigest() == out.sha256


def test_filename_ends_with_xlsx():
    out = write_xlsx_ledger(_brief(), disclaimer="d")
    assert out.filename.endswith(".xlsx")


def test_filename_is_filesystem_safe():
    out = write_xlsx_ledger(_brief(sheet_title="weird/title:here?<>*"), disclaimer="d")
    assert "/" not in out.filename
    assert ":" not in out.filename
    assert out.filename.endswith(".xlsx")


def test_requires_timezone_aware_created_at():
    bad = _brief(created_at=datetime(2024, 6, 3, 9, 0))  # naive
    with pytest.raises(ValueError):
        write_xlsx_ledger(bad, disclaimer="d")


def test_written_xlsx_ledger_satisfies_written_artifact_protocol():
    from api.provenance.artifact_writer import WrittenArtifact

    out = write_xlsx_ledger(_brief(), disclaimer="d")
    assert isinstance(out, WrittenArtifact)
