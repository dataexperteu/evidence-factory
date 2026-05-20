"""Provenance Catalog — PDF document profile golden tests.

Slice 3 acceptance criteria:
- PDF parses cleanly with an independent reader (pypdf)
- Metadata round-trips: Author == persona.full_name, CreationDate == event.timestamp
  (within metadata-resolution tolerance), ModDate >= CreationDate, Title is the
  event's content-brief subject
- SHA-256 stability across two writes given identical inputs
"""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from pypdf import PdfReader

from api.provenance.pdf_profile import PDFBrief, WrittenPDF, write_pdf

_DEFAULTS = dict(
    author="Sherlock Holmes",
    title="The Speckled Band — Investigation Report",
    body="Watson, I have discovered the key to this mystery.\nThe snake was the weapon.",
    creation_date=datetime(2024, 6, 3, 9, 15, tzinfo=UTC),
    mod_date=datetime(2024, 6, 3, 10, 0, tzinfo=UTC),
)


def _brief(**overrides: object) -> PDFBrief:
    merged = {**_DEFAULTS, **overrides}
    return PDFBrief(
        author=str(merged["author"]),
        title=str(merged["title"]),
        body=str(merged["body"]),
        creation_date=merged["creation_date"],  # type: ignore[arg-type]
        mod_date=merged["mod_date"],  # type: ignore[arg-type]
    )


def test_pdf_parses_cleanly_with_pypdf():
    import io

    out = write_pdf(_brief(), disclaimer="SYNTHETIC EVIDENCE — demo")
    reader = PdfReader(io.BytesIO(out.payload))
    assert len(reader.pages) >= 1


def test_pdf_metadata_author_equals_persona_full_name():
    out = write_pdf(_brief(author="John Watson"), disclaimer="d")
    import io

    meta = PdfReader(io.BytesIO(out.payload)).metadata
    assert meta.get("/Author") == "John Watson"


def test_pdf_metadata_title_equals_brief_title():
    import io

    out = write_pdf(_brief(title="A Contract for Baker Street"), disclaimer="d")
    meta = PdfReader(io.BytesIO(out.payload)).metadata
    assert meta.get("/Title") == "A Contract for Baker Street"


def test_pdf_metadata_creation_date_matches_event_timestamp():
    """CreationDate must encode the brief's creation_date to the second."""
    import io

    ts = datetime(2024, 6, 3, 9, 15, 0, tzinfo=UTC)
    out = write_pdf(_brief(creation_date=ts, mod_date=ts), disclaimer="d")
    meta = PdfReader(io.BytesIO(out.payload)).metadata
    cd = meta.get("/CreationDate", "")
    # PDF date: D:20240603091500+00'00'
    assert "20240603091500" in cd, f"expected timestamp in CreationDate, got {cd!r}"


def test_pdf_metadata_mod_date_gte_creation_date():
    import io

    creation = datetime(2024, 6, 3, 9, 15, tzinfo=UTC)
    mod = datetime(2024, 6, 3, 10, 30, tzinfo=UTC)
    out = write_pdf(_brief(creation_date=creation, mod_date=mod), disclaimer="d")
    meta = PdfReader(io.BytesIO(out.payload)).metadata
    cd = meta.get("/CreationDate", "")
    md = meta.get("/ModDate", "")
    # Both present; ModDate string >= CreationDate string (lexicographic on D:YYYYMMDD... works)
    assert cd and md
    assert md >= cd, f"ModDate {md!r} must be >= CreationDate {cd!r}"


def test_pdf_metadata_producer_is_evidence_factory():
    import io

    out = write_pdf(_brief(), disclaimer="d")
    meta = PdfReader(io.BytesIO(out.payload)).metadata
    assert meta.get("/Producer") == "Evidence Factory"


def test_sha256_matches_payload_bytes():
    out = write_pdf(_brief(), disclaimer="d")
    assert hashlib.sha256(out.payload).hexdigest() == out.sha256


def test_sha256_stability_across_two_writes():
    """Identical PDFBrief → identical SHA-256 (no clock or random in the writer)."""
    brief = _brief()
    w1 = write_pdf(brief, disclaimer="SYNTHETIC EVIDENCE — demo")
    w2 = write_pdf(brief, disclaimer="SYNTHETIC EVIDENCE — demo")
    assert w1.sha256 == w2.sha256, (
        f"SHA-256 differed between two identical writes: {w1.sha256} vs {w2.sha256}"
    )


def test_sha256_differs_for_different_briefs():
    w1 = write_pdf(_brief(title="Document A"), disclaimer="d")
    w2 = write_pdf(_brief(title="Document B"), disclaimer="d")
    assert w1.sha256 != w2.sha256


def test_filename_is_filesystem_safe():
    out = write_pdf(_brief(title="weird/title:name?<>*"), disclaimer="d")
    assert "/" not in out.filename
    assert ":" not in out.filename
    assert out.filename.endswith(".pdf")


def test_filename_encodes_creation_timestamp():
    ts = datetime(2024, 6, 3, 9, 15, 0, tzinfo=UTC)
    out = write_pdf(_brief(creation_date=ts, mod_date=ts), disclaimer="d")
    assert "20240603T091500Z" in out.filename


def test_requires_timezone_aware_creation_date():
    with pytest.raises(ValueError, match="timezone-aware"):
        write_pdf(
            _brief(creation_date=datetime(2024, 6, 3, 9, 15)),  # naive
            disclaimer="d",
        )


def test_requires_timezone_aware_mod_date():
    with pytest.raises(ValueError, match="timezone-aware"):
        write_pdf(
            _brief(mod_date=datetime(2024, 6, 3, 10, 0)),  # naive
            disclaimer="d",
        )


def test_requires_mod_date_gte_creation_date():
    creation = datetime(2024, 6, 3, 9, 15, tzinfo=UTC)
    mod_before = creation - timedelta(minutes=5)
    with pytest.raises(ValueError, match="mod_date"):
        write_pdf(_brief(creation_date=creation, mod_date=mod_before), disclaimer="d")


def test_returns_written_pdf_dataclass():
    out = write_pdf(_brief(), disclaimer="d")
    assert isinstance(out, WrittenPDF)
    assert out.filename
    assert out.payload
    assert out.sha256
