"""Synthetic-evidence watermark — per-profile disclaimer round-trip golden tests.

Slice 11 acceptance criterion: the disclaimer must be recoverable from a
*format-appropriate* field of every emitted artifact:

  - email           -> X-Synthetic-Evidence header
  - PDF             -> /Keywords
  - JPEG            -> EXIF UserComment
  - .xlsx           -> core property `keywords`
  - system_log_csv  -> leading `#` header comment
  - SMS             -> thread metadata field

These tests parse each artifact back through an independent reader and assert
the disclaimer survives, so a future change cannot silently drop the watermark.
"""

import io
from datetime import UTC, datetime

import pytest

DISCLAIMER = "SYNTHETIC EVIDENCE — round-trip watermark probe."
_TS = datetime(2024, 6, 3, 9, 15, 0, tzinfo=UTC)


def test_email_disclaimer_round_trips_via_header():
    import mailparser

    from api.provenance.email_profile import EmailBrief, write_email

    brief = EmailBrief(
        sender_name="Sherlock Holmes",
        sender_address="holmes@baker-street.example",
        recipients=(("John Watson", "watson@baker-street.example"),),
        subject="notes",
        body="body",
        sent_at=_TS,
    )
    out = write_email(brief, disclaimer=DISCLAIMER)
    parsed = mailparser.parse_from_bytes(out.payload)
    headers = {k.lower(): v for k, v in parsed.headers.items()}
    assert headers["x-synthetic-evidence"] == DISCLAIMER


def test_pdf_disclaimer_round_trips_via_keywords():
    import pypdf

    from api.provenance.pdf_profile import PdfBrief, write_pdf

    brief = PdfBrief(
        author_name="Sherlock Holmes",
        title="report",
        body="body",
        created_at=_TS,
    )
    out = write_pdf(brief, disclaimer=DISCLAIMER)
    reader = pypdf.PdfReader(io.BytesIO(out.payload))
    keywords = reader.metadata.get("/Keywords", "")
    assert DISCLAIMER in keywords


def test_jpeg_disclaimer_round_trips_via_exif_user_comment():
    pytest.importorskip("piexif")
    import piexif
    import piexif.helper

    from api.provenance.jpeg_profile import JpegBrief, write_jpeg

    brief = JpegBrief(timestamp=_TS, make="Apple", model="iPhone 15 Pro", caption="caption")
    out = write_jpeg(brief, disclaimer=DISCLAIMER)
    exif = piexif.load(out.payload)
    raw = exif["Exif"][piexif.ExifIFD.UserComment]
    assert piexif.helper.UserComment.load(raw) == DISCLAIMER


def test_xlsx_disclaimer_round_trips_via_keywords_property():
    import openpyxl

    from api.provenance.xlsx_ledger_profile import XlsxLedgerBrief, write_xlsx_ledger

    brief = XlsxLedgerBrief(
        creator_name="Alice Smith",
        created_at=_TS,
        headers=("date", "amount"),
        rows=((datetime(2024, 6, 1), 10.0),),
        sheet_title="Ledger",
    )
    out = write_xlsx_ledger(brief, disclaimer=DISCLAIMER)
    wb = openpyxl.load_workbook(io.BytesIO(out.payload), read_only=True)
    try:
        assert wb.properties.keywords == DISCLAIMER
    finally:
        wb.close()


def test_system_log_csv_disclaimer_round_trips_via_header_comment():
    from api.provenance.system_log_csv_profile import SystemLogBrief, write_system_log_csv

    brief = SystemLogBrief(
        schema="access_log",
        device_id="d_bldg_ctrl",
        event_id="ev_1",
        event_timestamp=_TS,
        persona_ids=("p_holmes", "p_watson"),
    )
    out = write_system_log_csv(brief, disclaimer=DISCLAIMER)
    first_line = out.payload.decode("utf-8").splitlines()[0]
    assert first_line.startswith("#")
    assert DISCLAIMER in first_line


def test_sms_disclaimer_round_trips_via_thread_metadata_field():
    from api.provenance.sms_profile import SmsBrief, write_sms

    brief = SmsBrief(
        sender_name="Sherlock Holmes",
        sender_number="+1-555-0001",
        recipient_name="John Watson",
        recipient_number="+1-555-0002",
        body="Holmes: found it.\n\nWatson: where?",
        sent_at=_TS,
    )
    out = write_sms(brief, disclaimer=DISCLAIMER)
    decoded = out.payload.decode("utf-8")
    meta = [ln for ln in decoded.splitlines() if ln.startswith("# Synthetic-Evidence:")]
    assert meta, "SMS thread metadata field missing"
    assert DISCLAIMER in meta[0]
