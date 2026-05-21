"""JPEG + EXIF photo profile — golden round-trip tests.

Mirrors the structure of test_email_profile.py: exercises the API-layer
jpeg_profile writer and validates the output with two independent EXIF
readers (piexif and exifread) plus Pillow.Image.open().
"""

from __future__ import annotations

import hashlib
import io
from datetime import UTC, datetime
from typing import Any

import pytest

from api.provenance.jpeg_profile import JpegBrief, WrittenJpeg, write_jpeg

DISCLAIMER = "SYNTHETIC EVIDENCE — for demonstration only"


def _brief(**overrides: Any) -> JpegBrief:
    defaults: dict[str, Any] = dict(
        timestamp=datetime(2024, 6, 3, 9, 15, tzinfo=UTC),
        make="Apple",
        model="iPhone 15 Pro",
        caption="A view of the study window at Baker Street.",
    )
    defaults.update(overrides)
    return JpegBrief(**defaults)


# ---------------------------------------------------------------------------
# Basic validity
# ---------------------------------------------------------------------------


def test_write_jpeg_returns_written_jpeg():
    out = write_jpeg(_brief(), disclaimer=DISCLAIMER)
    assert isinstance(out, WrittenJpeg)


def test_payload_starts_with_jpeg_magic():
    out = write_jpeg(_brief(), disclaimer=DISCLAIMER)
    assert out.payload[:2] == b"\xff\xd8", f"Expected JPEG SOI, got {out.payload[:2]!r}"


def test_sha256_matches_payload():
    out = write_jpeg(_brief(), disclaimer=DISCLAIMER)
    assert hashlib.sha256(out.payload).hexdigest() == out.sha256


def test_filename_ends_with_jpg():
    out = write_jpeg(_brief(), disclaimer=DISCLAIMER)
    assert out.filename.endswith(".jpg")


def test_filename_is_filesystem_safe():
    out = write_jpeg(_brief(), disclaimer=DISCLAIMER)
    assert "/" not in out.filename
    assert ":" not in out.filename


def test_requires_timezone_aware_timestamp():
    bad = _brief(timestamp=datetime(2024, 6, 3, 9, 15))  # naive
    with pytest.raises(ValueError):
        write_jpeg(bad, disclaimer=DISCLAIMER)


# ---------------------------------------------------------------------------
# Pillow round-trip
# ---------------------------------------------------------------------------


def test_pillow_can_open_output():
    pytest.importorskip("PIL")
    from PIL import Image

    out = write_jpeg(_brief(), disclaimer=DISCLAIMER)
    img = Image.open(io.BytesIO(out.payload))
    assert img.format == "JPEG"
    assert img.size == (320, 240)


# ---------------------------------------------------------------------------
# EXIF tags — piexif reader
# ---------------------------------------------------------------------------


def test_exif_datetime_original_matches_timestamp():
    pytest.importorskip("piexif")
    import piexif

    brief = _brief(timestamp=datetime(2024, 3, 15, 14, 22, 0, tzinfo=UTC))
    out = write_jpeg(brief, disclaimer=DISCLAIMER)
    exif = piexif.load(out.payload)
    dto = exif["Exif"].get(piexif.ExifIFD.DateTimeOriginal, b"").decode()
    assert dto == "2024:03:15 14:22:00"


def test_exif_make_model_from_brief():
    pytest.importorskip("piexif")
    import piexif

    out = write_jpeg(_brief(make="Canon", model="EOS R5"), disclaimer=DISCLAIMER)
    exif = piexif.load(out.payload)
    make = exif["0th"].get(piexif.ImageIFD.Make, b"").decode()
    model = exif["0th"].get(piexif.ImageIFD.Model, b"").decode()
    assert "Canon" in make
    assert "EOS R5" in model


def test_exif_gps_written_when_coordinates_supplied():
    pytest.importorskip("piexif")
    import piexif

    out = write_jpeg(_brief(gps_lat=51.5074, gps_lon=-0.1278), disclaimer=DISCLAIMER)
    exif = piexif.load(out.payload)
    assert piexif.GPSIFD.GPSLatitude in exif["GPS"]
    assert piexif.GPSIFD.GPSLongitude in exif["GPS"]
    assert exif["GPS"][piexif.GPSIFD.GPSLatitudeRef] == b"N"
    assert exif["GPS"][piexif.GPSIFD.GPSLongitudeRef] == b"W"


def test_exif_gps_south_west():
    pytest.importorskip("piexif")
    import piexif

    out = write_jpeg(_brief(gps_lat=-33.8688, gps_lon=151.2093), disclaimer=DISCLAIMER)
    exif = piexif.load(out.payload)
    assert exif["GPS"][piexif.GPSIFD.GPSLatitudeRef] == b"S"
    assert exif["GPS"][piexif.GPSIFD.GPSLongitudeRef] == b"E"


def test_exif_no_gps_when_coordinates_absent():
    pytest.importorskip("piexif")
    import piexif

    out = write_jpeg(_brief(), disclaimer=DISCLAIMER)
    exif = piexif.load(out.payload)
    assert not exif.get("GPS") or piexif.GPSIFD.GPSLatitude not in exif["GPS"]


# ---------------------------------------------------------------------------
# Two-library EXIF round-trip: piexif and exifread must agree
# ---------------------------------------------------------------------------


def test_exif_two_library_round_trip():
    """piexif and exifread must read DateTimeOriginal identically."""
    pytest.importorskip("piexif")
    pytest.importorskip("exifread")
    import exifread
    import piexif

    dt = datetime(2024, 6, 1, 9, 0, 0, tzinfo=UTC)
    out = write_jpeg(
        _brief(timestamp=dt, make="Canon", model="EOS R5", gps_lat=51.5, gps_lon=-0.1),
        disclaimer=DISCLAIMER,
    )
    expected_dt = "2024:06:01 09:00:00"

    # Reader 1: piexif
    exif1 = piexif.load(out.payload)
    dto_piexif = exif1["Exif"].get(piexif.ExifIFD.DateTimeOriginal, b"").decode()

    # Reader 2: exifread
    tags = exifread.process_file(io.BytesIO(out.payload), details=False)
    dto_exifread = str(tags.get("EXIF DateTimeOriginal", ""))

    assert dto_piexif == expected_dt, f"piexif: {dto_piexif!r}"
    assert dto_exifread == expected_dt, f"exifread: {dto_exifread!r}"
    assert dto_piexif == dto_exifread


def test_exif_gps_rational_triples_are_valid():
    """GPS coordinates must be expressed as three rational pairs (deg, min, sec)."""
    pytest.importorskip("piexif")
    import piexif

    lat, lon = 51.5074, -0.1278
    out = write_jpeg(_brief(gps_lat=lat, gps_lon=lon), disclaimer=DISCLAIMER)
    exif = piexif.load(out.payload)
    gps_lat = exif["GPS"][piexif.GPSIFD.GPSLatitude]
    gps_lon = exif["GPS"][piexif.GPSIFD.GPSLongitude]

    # Each coordinate must be a tuple of 3 rational pairs
    assert len(gps_lat) == 3
    assert len(gps_lon) == 3
    for (num, den) in gps_lat:
        assert isinstance(num, int) and isinstance(den, int) and den > 0
    for (num, den) in gps_lon:
        assert isinstance(num, int) and isinstance(den, int) and den > 0
