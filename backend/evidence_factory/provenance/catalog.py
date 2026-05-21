from __future__ import annotations

import csv
import io
from email.message import EmailMessage
from typing import Any

from ..models import ArtifactProfile


def _pdf_bytes(text: str, author: str, created: str) -> bytes:
    """Build a minimal valid PDF without external dependencies."""

    def _escape(s: str) -> str:
        return s[:600].replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    stream_src = f"BT /F1 10 Tf 40 750 Td ({_escape(text)}) Tj ET"
    stream = stream_src.encode("latin-1", errors="replace")
    stream_len = len(stream)

    raw_objs: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
            b" /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        (f"<< /Length {stream_len} >>\nstream\n".encode() + stream + b"\nendstream"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    body = b"%PDF-1.4\n"
    offsets: list[int] = []
    for i, obj in enumerate(raw_objs, 1):
        offsets.append(len(body))
        body += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"

    xref_pos = len(body)
    xref_lines = [f"xref\n0 {len(raw_objs) + 1}\n", "0000000000 65535 f \n"]
    for off in offsets:
        xref_lines.append(f"{off:010d} 00000 n \n")
    trailer = (
        f"trailer\n<< /Size {len(raw_objs) + 1} /Root 1 0 R"
        f" /Info << /Author ({_escape(author)}) /CreationDate ({_escape(created)}) >> >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    )
    xref_lines.append(trailer)
    return body + "".join(xref_lines).encode()


class ProvenanceCatalog:
    """Writes noise/signal artifacts through one of six format profiles."""

    def write(
        self,
        profile: ArtifactProfile,
        text_content: str,
        metadata: dict[str, Any],
    ) -> bytes:
        dispatch = {
            ArtifactProfile.EMAIL: self._write_email,
            ArtifactProfile.SMS: self._write_sms,
            ArtifactProfile.PDF: self._write_pdf,
            ArtifactProfile.XLSX: self._write_xlsx,
            ArtifactProfile.JPEG: self._write_jpeg,
            ArtifactProfile.LOG: self._write_log,
        }
        return dispatch[profile](text_content, metadata)

    # ------------------------------------------------------------------
    # Email (.eml)
    # ------------------------------------------------------------------

    def _write_email(self, text_content: str, metadata: dict[str, Any]) -> bytes:
        msg = EmailMessage()
        msg["From"] = str(metadata.get("from", "sender@example.com"))
        msg["To"] = str(metadata.get("to", "recipient@example.com"))
        msg["Subject"] = str(metadata.get("subject", "No Subject"))
        msg["Message-ID"] = str(
            metadata.get("message_id", f"<{id(text_content)}@evidence-factory.local>")
        )
        if "date" in metadata:
            msg["Date"] = str(metadata["date"])
        msg.set_content(text_content)
        return bytes(msg)

    # ------------------------------------------------------------------
    # SMS / chat export (CSV)
    # ------------------------------------------------------------------

    def _write_sms(self, text_content: str, metadata: dict[str, Any]) -> bytes:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["timestamp", "sender", "recipient", "message"])
        sender = str(metadata.get("sender", "unknown"))
        recipient = str(metadata.get("recipient", "unknown"))
        ts = str(metadata.get("timestamp", ""))
        for line in text_content.splitlines():
            if line.strip():
                writer.writerow([ts, sender, recipient, line])
        return buf.getvalue().encode("utf-8")

    # ------------------------------------------------------------------
    # PDF document (stdlib — no reportlab dependency)
    # ------------------------------------------------------------------

    def _write_pdf(self, text_content: str, metadata: dict[str, Any]) -> bytes:
        author = str(metadata.get("author", "Unknown"))
        created = str(metadata.get("created", "D:20240101000000"))
        return _pdf_bytes(text_content, author, created)

    # ------------------------------------------------------------------
    # XLSX ledger (openpyxl)
    # ------------------------------------------------------------------

    def _write_xlsx(self, text_content: str, metadata: dict[str, Any]) -> bytes:
        try:
            import openpyxl
            from openpyxl import Workbook

            wb: Workbook = openpyxl.Workbook()
            ws = wb.active
            if ws is None:
                ws = wb.create_sheet()
            ws.title = str(metadata.get("sheet_title", "Sheet1"))
            wb.properties.creator = str(metadata.get("author", "Evidence Factory"))
            ws.append(["timestamp", "entry"])
            for line in text_content.splitlines():
                if line.strip():
                    ts = str(metadata.get("timestamp", ""))
                    ws.append([ts, line])
            buf = io.BytesIO()
            wb.save(buf)
            return buf.getvalue()
        except ImportError:
            return self._write_log(text_content, metadata)

    # ------------------------------------------------------------------
    # JPEG photo (Pillow + piexif for EXIF)
    # ------------------------------------------------------------------

    def _write_jpeg(self, text_content: str, metadata: dict[str, Any]) -> bytes:
        try:
            import piexif
            from PIL import Image, ImageDraw, ImageFont

            width, height = 320, 240
            img = Image.new("RGB", (width, height), color=(200, 210, 220))
            draw = ImageDraw.Draw(img)
            try:
                font: ImageFont.ImageFont | ImageFont.FreeTypeFont = ImageFont.load_default()
            except Exception:
                font = ImageFont.load_default()
            draw.text((10, 10), text_content[:80], fill=(40, 40, 40), font=font)

            dt_str = str(metadata.get("datetime", "2024:01:15 10:30:00"))
            exif_dict: dict[str, Any] = {
                "0th": {
                    piexif.ImageIFD.DateTime: dt_str.encode(),
                    piexif.ImageIFD.Make: str(metadata.get("make", "EvidenceFactory")).encode(),
                    piexif.ImageIFD.Model: str(
                        metadata.get("model", metadata.get("device", "Device"))
                    ).encode(),
                },
                "Exif": {
                    piexif.ExifIFD.DateTimeOriginal: dt_str.encode(),
                    piexif.ExifIFD.DateTimeDigitized: dt_str.encode(),
                },
                "GPS": {},
                "1st": {},
                "thumbnail": None,
            }
            if "gps_lat" in metadata and "gps_lon" in metadata:

                def _deg(v: float) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]]:
                    deg = int(abs(v))
                    mn = int((abs(v) - deg) * 60)
                    sec = int(((abs(v) - deg) * 60 - mn) * 60 * 100)
                    return ((deg, 1), (mn, 1), (sec, 100))

                lat = float(metadata["gps_lat"])
                lon = float(metadata["gps_lon"])
                exif_dict["GPS"] = {
                    piexif.GPSIFD.GPSLatitudeRef: b"N" if lat >= 0 else b"S",
                    piexif.GPSIFD.GPSLatitude: _deg(lat),
                    piexif.GPSIFD.GPSLongitudeRef: b"E" if lon >= 0 else b"W",
                    piexif.GPSIFD.GPSLongitude: _deg(lon),
                }

            exif_bytes = piexif.dump(exif_dict)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", exif=exif_bytes)
            return buf.getvalue()
        except ImportError:
            return self._write_log(text_content, metadata)

    # ------------------------------------------------------------------
    # Log / CSV (access logs / call-detail records)
    # ------------------------------------------------------------------

    def _write_log(self, text_content: str, metadata: dict[str, Any]) -> bytes:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["timestamp", "level", "source", "message"])
        source = str(metadata.get("source", "system"))
        ts = str(metadata.get("timestamp", ""))
        for line in text_content.splitlines():
            if line.strip():
                writer.writerow([ts, "INFO", source, line])
        return buf.getvalue().encode("utf-8")
