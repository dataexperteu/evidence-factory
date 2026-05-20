"""JPEG + EXIF photo profile — slice 6.

Uses `Pillow` to encode the JPEG (solid-colour fill; image realism is not
required) and `piexif` to embed a valid EXIF block containing:
  - DateTimeOriginal  → event timestamp
  - Make / Model      → persona's device make/model
  - GPSInfo           → rational triples for lat/lon when device is GPS-capable
                        and the event carries a location

EXIF realism (correct rational encoding, round-trippable tags) is what
matters; the Provenance Catalog tests verify round-trip with two independent
readers: piexif (writer-side) and exifread (independent reader).
"""

from __future__ import annotations

import hashlib
import io
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

import piexif
from PIL import Image


@dataclass(frozen=True)
class PhotoBrief:
    """Content brief handed to the JPEG photo profile writer."""

    timestamp: datetime
    make: str
    model: str
    gps_capable: bool
    location: tuple[float, float] | None  # (lat, lon) decimal degrees


@dataclass(frozen=True)
class WrittenPhoto:
    filename: str
    payload: bytes
    sha256: str


def _to_rational_triple(
    decimal: float,
) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]]:
    """Convert a non-negative decimal degree value to an EXIF rational triple.

    EXIF GPS coordinates are (degrees, minutes, seconds) where each component
    is a (numerator, denominator) rational pair. Seconds are stored with
    1/100 precision to keep denominators small and universally readable.
    """
    d = int(decimal)
    remaining = (decimal - d) * 60
    m = int(remaining)
    s = (remaining - m) * 60
    return ((d, 1), (m, 1), (round(s * 100), 100))


def _pick_fill_colour(ts: datetime) -> tuple[int, int, int]:
    """Derive a deterministic-but-varied solid fill colour from the timestamp."""
    h = ts.hour
    return (80 + (h * 7 % 120), 60 + (h * 13 % 120), 90 + (h * 11 % 100))


def write_jpeg(brief: PhotoBrief) -> WrittenPhoto:
    """Serialise a PhotoBrief to JPEG bytes with embedded EXIF.

    Image content is a solid-colour fill derived from the timestamp — adequate
    for corpus generation; image realism is not what the AI under test scores.
    """
    if brief.timestamp.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")

    # --- image body ---
    fill = _pick_fill_colour(brief.timestamp)
    img = Image.new("RGB", (320, 240), color=fill)

    # --- EXIF construction ---
    dt_str = brief.timestamp.astimezone(UTC).strftime("%Y:%m:%d %H:%M:%S").encode()
    zeroth_ifd: dict = {
        piexif.ImageIFD.Make: brief.make.encode() if brief.make else b"Unknown",
        piexif.ImageIFD.Model: brief.model.encode() if brief.model else b"Unknown",
    }
    exif_ifd: dict = {
        piexif.ExifIFD.DateTimeOriginal: dt_str,
    }
    gps_ifd: dict = {}

    if brief.gps_capable and brief.location is not None:
        lat, lon = brief.location
        lat_ref = b"N" if lat >= 0 else b"S"
        lon_ref = b"E" if lon >= 0 else b"W"
        gps_ifd = {
            piexif.GPSIFD.GPSLatitudeRef: lat_ref,
            piexif.GPSIFD.GPSLatitude: _to_rational_triple(abs(lat)),
            piexif.GPSIFD.GPSLongitudeRef: lon_ref,
            piexif.GPSIFD.GPSLongitude: _to_rational_triple(abs(lon)),
        }

    exif_bytes = piexif.dump({"0th": zeroth_ifd, "Exif": exif_ifd, "GPS": gps_ifd})

    # --- serialise ---
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif_bytes, quality=85)
    payload = buf.getvalue()
    sha = hashlib.sha256(payload).hexdigest()

    suffix = secrets.token_hex(3)
    ts_utc = brief.timestamp.astimezone(UTC)
    ts_str = ts_utc.strftime("%Y%m%dT%H%M%SZ")
    filename = f"{ts_str}_{suffix}.jpg"

    return WrittenPhoto(filename=filename, payload=payload, sha256=sha)
