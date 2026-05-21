"""PDF document profile — slice 3.

Uses reportlab to produce structurally valid PDFs whose document metadata
(/Producer, /Author, /CreationDate, /ModDate, /Title) is consistent with the
owning persona and the event timestamp.

Determinism guarantee: passing `invariant=True` to Canvas stabilises the
reportlab trailer timestamp, and we inject all metadata from the PdfBrief so
that identical inputs always produce identical bytes (and thus identical
SHA-256 digests).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from reportlab.lib.pagesizes import letter
from reportlab.pdfbase.pdfdoc import PDFDictionary, PDFInfo, PDFString
from reportlab.pdfgen import canvas

_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")

_PRODUCER = "Evidence Factory PDF Profile"


def _slug(s: str, max_len: int = 40) -> str:
    s = _SAFE.sub("-", s).strip("-")
    return (s[:max_len] or "document").lower()


def _pdf_date(dt: datetime) -> str:
    """PDF date string (ISO 8824 / PDF §7.9.4)."""
    utc = dt.astimezone(UTC)
    return utc.strftime("D:%Y%m%d%H%M%S+00'00'")


class _FixedPDFInfo(PDFInfo):
    """PDFInfo subclass that injects caller-supplied dates instead of time.time()."""

    def __init__(
        self,
        *,
        author: str,
        title: str,
        subject: str,
        creation_date: str,
        mod_date: str,
        keywords: str = "",
    ) -> None:
        super().__init__()
        self.author = author
        self.title = title
        self.subject = subject
        self.creator = _PRODUCER
        self.producer = _PRODUCER
        self.keywords = keywords
        self._creation_date = creation_date
        self._mod_date = mod_date

    def format(self, document: object) -> str:  # type: ignore[override]
        d: dict[str, object] = {
            "Author": PDFString(self.author),
            "Title": PDFString(self.title),
            "Subject": PDFString(self.subject),
            "Creator": PDFString(self.creator),
            "Producer": PDFString(self.producer),
            "Keywords": PDFString(self.keywords),
            "CreationDate": PDFString(self._creation_date),
            "ModDate": PDFString(self._mod_date),
        }
        return PDFDictionary(d).format(document)  # type: ignore[arg-type]


@dataclass(frozen=True)
class PdfBrief:
    """Content brief handed to the PDF profile writer."""

    author_name: str
    title: str
    body: str
    created_at: datetime


@dataclass(frozen=True)
class WrittenPdf:
    filename: str
    payload: bytes
    sha256: str


def write_pdf(brief: PdfBrief, *, disclaimer: str) -> WrittenPdf:
    """Serialise a PdfBrief to a reportlab-generated PDF bytes object.

    `disclaimer` is stamped into the PDF Keywords field so the
    chain-of-custody manifest and the file both carry it.

    The output is deterministic: two calls with identical `brief` and
    `disclaimer` produce the same SHA-256 digest.
    """
    if brief.created_at.tzinfo is None:
        raise ValueError("created_at must be timezone-aware")

    created_pdf = _pdf_date(brief.created_at)
    keywords = f"SYNTHETIC-EVIDENCE; {disclaimer}"

    import io

    buf = io.BytesIO()
    # invariant=True stabilises the trailer timestamp so output is deterministic.
    c = canvas.Canvas(buf, pagesize=letter, invariant=True)
    c._doc.info = _FixedPDFInfo(
        author=brief.author_name,
        title=brief.title,
        subject=brief.title,
        creation_date=created_pdf,
        mod_date=created_pdf,
        keywords=keywords,
    )

    # Title block
    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, 750, brief.title[:80])

    # Body text — wrap naively at 90 chars per line
    c.setFont("Helvetica", 10)
    y = 720
    for para in brief.body.splitlines():
        # Split long lines at 90-char boundary
        while para:
            chunk, para = para[:90], para[90:]
            c.drawString(72, y, chunk)
            y -= 14
            if y < 72:
                c.showPage()
                y = 750

    # Disclaimer footer on last page
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(72, 40, f"SYNTHETIC EVIDENCE — {disclaimer}"[:110])

    c.save()
    payload = buf.getvalue()

    sha = hashlib.sha256(payload).hexdigest()
    ts = brief.created_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    filename = f"{ts}_{_slug(brief.title)}.pdf"
    return WrittenPdf(filename=filename, payload=payload, sha256=sha)
