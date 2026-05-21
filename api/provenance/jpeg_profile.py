"""JPEG + EXIF photo profile.

Builds a valid JPEG with Pillow and embeds a well-formed EXIF block via piexif.
Make, Model, and DateTimeOriginal are mandatory; GPSInfo is written only when
coordinates are explicitly supplied.

The image content is a solid-colour fill — EXIF realism, not visual realism, is
what matters for the corpus.
"""

from __future__ import annotations

import hashlib
import io
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class JpegBrief:
    """Content brief handed to the JPEG profile writer."""

    timestamp: datetime
    make: str
    model: str
    caption: str
    gps_lat: float | None = None
    gps_lon: float | None = None


@dataclass(frozen=True)
class WrittenJpeg:
    filename: str
    payload: bytes
    sha256: str


def _exif_dt(dt: datetime) -> str:
    return dt.strftime("%Y:%m:%d %H:%M:%S")


def _rational_deg(v: float) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]]:
    deg = int(abs(v))
    mn = int((abs(v) - deg) * 60)
    sec = int(((abs(v) - deg) * 60 - mn) * 60 * 100)
    return ((deg, 1), (mn, 1), (sec, 100))


def write_jpeg(brief: JpegBrief, *, disclaimer: str) -> WrittenJpeg:
    """Serialise a JpegBrief to JPEG bytes with a valid EXIF block.

    `disclaimer` is stamped into ImageDescription so every artifact carries
    the synthetic-evidence label.
    """
    if brief.timestamp.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")

    import piexif
    from PIL import Image, ImageDraw, ImageFont

    width, height = 320, 240
    img = Image.new("RGB", (width, height), color=(200, 210, 220))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()
    draw.text((10, 10), brief.caption[:80], fill=(40, 40, 40), font=font)

    dt_str = _exif_dt(brief.timestamp)
    exif_dict: dict = {
        "0th": {
            piexif.ImageIFD.DateTime: dt_str.encode(),
            piexif.ImageIFD.Make: brief.make.encode()[:31],
            piexif.ImageIFD.Model: brief.model.encode()[:63],
            piexif.ImageIFD.ImageDescription: disclaimer.encode()[:255],
        },
        "Exif": {
            piexif.ExifIFD.DateTimeOriginal: dt_str.encode(),
            piexif.ExifIFD.DateTimeDigitized: dt_str.encode(),
        },
        "GPS": {},
        "1st": {},
        "thumbnail": None,
    }

    if brief.gps_lat is not None and brief.gps_lon is not None:
        lat, lon = brief.gps_lat, brief.gps_lon
        exif_dict["GPS"] = {
            piexif.GPSIFD.GPSLatitudeRef: b"N" if lat >= 0 else b"S",
            piexif.GPSIFD.GPSLatitude: _rational_deg(lat),
            piexif.GPSIFD.GPSLongitudeRef: b"E" if lon >= 0 else b"W",
            piexif.GPSIFD.GPSLongitude: _rational_deg(lon),
        }

    exif_bytes = piexif.dump(exif_dict)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif_bytes)
    payload = buf.getvalue()
    sha = hashlib.sha256(payload).hexdigest()

    ts = brief.timestamp.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = secrets.token_hex(3)
    filename = f"{ts}_{suffix}.jpg"
    return WrittenJpeg(filename=filename, payload=payload, sha256=sha)
