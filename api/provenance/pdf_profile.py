"""PDF document profile.

Uses reportlab for content layout and pypdf for deterministic metadata injection.
The ``invariant=1`` Canvas flag removes all system-clock calls from reportlab;
the pypdf post-pass replaces the /Info dictionary wholesale so Author, Title,
CreationDate, and ModDate reflect the brief exactly.

SHA-256 stability: identical PDFBrief → identical bytes, guaranteed by the
combination of invariant mode (no date/ID randomness) and explicit metadata
replacement (no auto-generated fields remain).
"""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


@dataclass(frozen=True)
class PDFBrief:
    """Content brief handed to the PDF document profile writer."""

    author: str
    title: str
    body: str
    creation_date: datetime  # must be timezone-aware
    mod_date: datetime  # must be >= creation_date


@dataclass(frozen=True)
class WrittenPDF:
    filename: str
    payload: bytes
    sha256: str


_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")


def _slug(s: str, max_len: int = 40) -> str:
    s = _SAFE.sub("-", s).strip("-")
    return (s[:max_len] or "document").lower()


def _pdf_date(dt: datetime) -> str:
    """Format a datetime as a PDF date string D:YYYYMMDDHHmmSS+HH'mm'."""
    utc = dt.astimezone(UTC)
    return f"D:{utc.strftime('%Y%m%d%H%M%S')}+00'00'"


def write_pdf(brief: PDFBrief, *, disclaimer: str) -> WrittenPDF:
    """Serialise a PDFBrief to PDF bytes with correct document metadata.

    Phase 1: reportlab with invariant=1 generates content without any
    clock-based values (dates and document IDs are fixed constants).
    Phase 2: pypdf replaces the entire /Info dictionary with values derived
    solely from the brief, yielding SHA-256 stability for identical inputs.
    """
    if brief.creation_date.tzinfo is None:
        raise ValueError("creation_date must be timezone-aware")
    if brief.mod_date.tzinfo is None:
        raise ValueError("mod_date must be timezone-aware")
    if brief.mod_date < brief.creation_date:
        raise ValueError("mod_date must be >= creation_date")

    # --- Phase 1: reportlab content (invariant=1 → no system clock) ---
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)

    page_w, page_h = A4
    margin = 72  # 1 inch

    # Title block
    c.setFont("Helvetica-Bold", 16)
    c.drawString(margin, page_h - margin, brief.title[:90])

    # Author / date line
    c.setFont("Helvetica-Oblique", 10)
    ts_label = brief.creation_date.astimezone(UTC).strftime("%Y-%m-%d")
    c.drawString(margin, page_h - margin - 20, f"{brief.author}  ·  {ts_label}")

    # Horizontal rule
    c.setLineWidth(0.5)
    rule_y = page_h - margin - 30
    c.line(margin, rule_y, page_w - margin, rule_y)

    # Body text — naive word-wrap, soft page break near bottom margin
    c.setFont("Helvetica", 11)
    y = rule_y - 18
    max_width = page_w - 2 * margin

    for paragraph in brief.body.split("\n"):
        words = paragraph.split()
        if not words:
            y -= 8
            if y < margin:
                c.showPage()
                c.setFont("Helvetica", 11)
                y = page_h - margin
            continue
        line_buf: list[str] = []
        for word in words:
            candidate = " ".join(line_buf + [word])
            if c.stringWidth(candidate, "Helvetica", 11) > max_width:
                if line_buf:
                    c.drawString(margin, y, " ".join(line_buf))
                    y -= 16
                    if y < margin:
                        c.showPage()
                        c.setFont("Helvetica", 11)
                        y = page_h - margin
                line_buf = [word]
            else:
                line_buf.append(word)
        if line_buf:
            c.drawString(margin, y, " ".join(line_buf))
            y -= 16
            if y < margin:
                c.showPage()
                c.setFont("Helvetica", 11)
                y = page_h - margin
        y -= 4  # paragraph spacing

    # Footer disclaimer
    c.setFont("Helvetica-Oblique", 7)
    c.drawString(margin, 20, disclaimer[:120])

    c.save()
    raw_bytes = buf.getvalue()

    # --- Phase 2: metadata injection via pypdf ---
    reader = PdfReader(io.BytesIO(raw_bytes))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.add_metadata(
        {
            "/Author": brief.author,
            "/Title": brief.title,
            "/Producer": "Evidence Factory",
            "/Creator": "Evidence Factory",
            "/CreationDate": _pdf_date(brief.creation_date),
            "/ModDate": _pdf_date(brief.mod_date),
            "/Subject": disclaimer[:200],
        }
    )
    out = io.BytesIO()
    writer.write(out)
    payload = out.getvalue()

    ts_str = brief.creation_date.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    filename = f"{ts_str}_{_slug(brief.title)}.pdf"
    sha = hashlib.sha256(payload).hexdigest()
    return WrittenPDF(filename=filename, payload=payload, sha256=sha)
