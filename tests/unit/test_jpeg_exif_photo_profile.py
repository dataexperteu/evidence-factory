"""Provenance Catalog — JPEG + EXIF photo profile golden round-trip.

Acceptance criteria verified here:
1. Emissions decode cleanly with Pillow.Image.open().
2. EXIF round-trips via two independent libraries: piexif (writer) and
   exifread (independent reader).
3. DateTimeOriginal matches the event timestamp.
4. Make + Model match the persona's device profile.
5. GPS-capable device: GPSLatitude / GPSLongitude are valid rational triples
   consistent with the supplied location.
6. GPS-uncertain (gps_capable=False) device: no GPS tags written.
7. Artifact Emitter routes jpeg_exif_photo events to the JPEG writer.
"""

from __future__ import annotations

import hashlib
import io
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import exifread
import piexif
import pytest
from PIL import Image

from api.pipeline.artifact_emitter import emit_artifacts
from api.pipeline.persona_registry import PersonaRegistry
from api.pipeline.signal_ledger import SignalLedger
from api.pipeline.types import Device, Event, Persona
from api.provenance.jpeg_exif_photo_profile import PhotoBrief, write_jpeg

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TS = datetime(2024, 7, 15, 14, 30, 0, tzinfo=UTC)
_LOCATION = (51.5074, -0.1278)  # London, lat N / lon W


def _brief(**overrides: Any) -> PhotoBrief:
    defaults: dict[str, Any] = dict(
        timestamp=_TS,
        make="Apple",
        model="iPhone 13",
        gps_capable=True,
        location=_LOCATION,
    )
    defaults.update(overrides)
    return PhotoBrief(**defaults)


def _exifread_tags(payload: bytes) -> dict[str, Any]:
    return exifread.process_file(io.BytesIO(payload), details=False)


def _piexif_tags(payload: bytes) -> dict:
    return piexif.load(payload)


def _pillow_exif(payload: bytes) -> dict:
    img = Image.open(io.BytesIO(payload))
    return img._getexif() or {}  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# JPEG validity
# ---------------------------------------------------------------------------


def test_jpeg_opens_with_pillow():
    out = write_jpeg(_brief())
    img = Image.open(io.BytesIO(out.payload))
    assert img.format == "JPEG"
    assert img.size == (320, 240)


def test_filename_has_jpg_extension():
    out = write_jpeg(_brief())
    assert out.filename.endswith(".jpg")
    assert "/" not in out.filename


def test_sha256_matches_payload():
    out = write_jpeg(_brief())
    assert hashlib.sha256(out.payload).hexdigest() == out.sha256


def test_requires_timezone_aware_timestamp():
    bad = _brief(timestamp=datetime(2024, 7, 15, 14, 30, 0))  # naive
    with pytest.raises(ValueError, match="timezone"):
        write_jpeg(bad)


# ---------------------------------------------------------------------------
# EXIF tag correctness — DateTimeOriginal
# ---------------------------------------------------------------------------


def test_exif_datetimeoriginal_matches_timestamp_via_piexif():
    out = write_jpeg(_brief())
    tags = _piexif_tags(out.payload)
    raw = tags["Exif"][piexif.ExifIFD.DateTimeOriginal]
    assert raw == b"2024:07:15 14:30:00"


def test_exif_datetimeoriginal_matches_timestamp_via_exifread():
    out = write_jpeg(_brief())
    tags = _exifread_tags(out.payload)
    assert "EXIF DateTimeOriginal" in tags
    assert str(tags["EXIF DateTimeOriginal"]) == "2024:07:15 14:30:00"


def test_exif_datetimeoriginal_non_utc_converted_to_utc():
    """Timestamps in other zones are normalised to UTC before writing."""
    ts = datetime(2024, 7, 15, 16, 30, 0, tzinfo=timezone(timedelta(hours=2)))
    out = write_jpeg(_brief(timestamp=ts))
    tags = _piexif_tags(out.payload)
    raw = tags["Exif"][piexif.ExifIFD.DateTimeOriginal]
    assert raw == b"2024:07:15 14:30:00"


# ---------------------------------------------------------------------------
# EXIF tag correctness — Make / Model
# ---------------------------------------------------------------------------


def test_exif_make_matches_device_via_piexif():
    out = write_jpeg(_brief(make="Canon", model="EOS 80D"))
    tags = _piexif_tags(out.payload)
    assert tags["0th"][piexif.ImageIFD.Make] == b"Canon"


def test_exif_model_matches_device_via_piexif():
    out = write_jpeg(_brief(make="Canon", model="EOS 80D"))
    tags = _piexif_tags(out.payload)
    assert tags["0th"][piexif.ImageIFD.Model] == b"EOS 80D"


def test_exif_make_model_via_exifread():
    out = write_jpeg(_brief(make="Samsung", model="Galaxy S21"))
    tags = _exifread_tags(out.payload)
    assert str(tags["Image Make"]) == "Samsung"
    assert str(tags["Image Model"]) == "Galaxy S21"


# ---------------------------------------------------------------------------
# EXIF round-trip: two independent libraries agree
# ---------------------------------------------------------------------------


def test_exif_reads_identically_with_pillow_and_exifread():
    """The key acceptance criterion: same EXIF content seen from both readers."""
    out = write_jpeg(_brief())

    piexif_tags = _piexif_tags(out.payload)
    exifread_tags = _exifread_tags(out.payload)

    # DateTimeOriginal agreement
    piexif_dt = piexif_tags["Exif"][piexif.ExifIFD.DateTimeOriginal].decode()
    exifread_dt = str(exifread_tags["EXIF DateTimeOriginal"])
    assert piexif_dt == exifread_dt

    # Make agreement
    piexif_make = piexif_tags["0th"][piexif.ImageIFD.Make].decode().rstrip("\x00")
    exifread_make = str(exifread_tags["Image Make"])
    assert piexif_make == exifread_make

    # Model agreement
    piexif_model = piexif_tags["0th"][piexif.ImageIFD.Model].decode().rstrip("\x00")
    exifread_model = str(exifread_tags["Image Model"])
    assert piexif_model == exifread_model


# ---------------------------------------------------------------------------
# GPS rational triples — GPS-capable device
# ---------------------------------------------------------------------------


def test_gps_capable_device_writes_gps_tags():
    out = write_jpeg(_brief(gps_capable=True, location=(51.5074, -0.1278)))
    tags = _piexif_tags(out.payload)
    gps = tags["GPS"]
    assert piexif.GPSIFD.GPSLatitude in gps
    assert piexif.GPSIFD.GPSLongitude in gps
    assert piexif.GPSIFD.GPSLatitudeRef in gps
    assert piexif.GPSIFD.GPSLongitudeRef in gps


def test_gps_latitude_ref_north_south():
    out_n = write_jpeg(_brief(location=(51.5074, 0.0)))
    out_s = write_jpeg(_brief(location=(-33.8688, 0.0)))
    assert _piexif_tags(out_n.payload)["GPS"][piexif.GPSIFD.GPSLatitudeRef] == b"N"
    assert _piexif_tags(out_s.payload)["GPS"][piexif.GPSIFD.GPSLatitudeRef] == b"S"


def test_gps_longitude_ref_east_west():
    out_e = write_jpeg(_brief(location=(0.0, 139.6917)))
    out_w = write_jpeg(_brief(location=(0.0, -0.1278)))
    assert _piexif_tags(out_e.payload)["GPS"][piexif.GPSIFD.GPSLongitudeRef] == b"E"
    assert _piexif_tags(out_w.payload)["GPS"][piexif.GPSIFD.GPSLongitudeRef] == b"W"


def test_gps_latitude_rational_triple_is_valid():
    """Rational triple: 3 tuples of (numerator, denominator) with denominator > 0."""
    out = write_jpeg(_brief(location=(51.5074, -0.1278)))
    lat = _piexif_tags(out.payload)["GPS"][piexif.GPSIFD.GPSLatitude]
    assert len(lat) == 3
    for n, d in lat:
        assert isinstance(n, int) and isinstance(d, int)
        assert d > 0


def test_gps_latitude_decodes_to_correct_value():
    """Decode rational triple back to decimal degrees and check accuracy (±0.01°)."""
    lat_in = 51.5074
    out = write_jpeg(_brief(location=(lat_in, -0.1278)))
    lat_triple = _piexif_tags(out.payload)["GPS"][piexif.GPSIFD.GPSLatitude]
    deg = lat_triple[0][0] / lat_triple[0][1]
    mins = lat_triple[1][0] / lat_triple[1][1]
    secs = lat_triple[2][0] / lat_triple[2][1]
    decoded = deg + mins / 60 + secs / 3600
    assert abs(decoded - lat_in) < 0.01


def test_gps_longitude_decodes_to_correct_value():
    lon_in = 139.6917
    out = write_jpeg(_brief(location=(35.6895, lon_in)))
    lon_triple = _piexif_tags(out.payload)["GPS"][piexif.GPSIFD.GPSLongitude]
    deg = lon_triple[0][0] / lon_triple[0][1]
    mins = lon_triple[1][0] / lon_triple[1][1]
    secs = lon_triple[2][0] / lon_triple[2][1]
    decoded = deg + mins / 60 + secs / 3600
    assert abs(decoded - lon_in) < 0.01


def test_gps_tags_visible_via_exifread():
    out = write_jpeg(_brief(gps_capable=True, location=(51.5074, -0.1278)))
    tags = _exifread_tags(out.payload)
    assert "GPS GPSLatitude" in tags
    assert "GPS GPSLongitude" in tags
    assert "GPS GPSLatitudeRef" in tags
    assert "GPS GPSLongitudeRef" in tags


# ---------------------------------------------------------------------------
# GPS-uncertain (gps_capable=False) device
# ---------------------------------------------------------------------------


def test_gps_uncertain_device_omits_gps_tags():
    out = write_jpeg(_brief(gps_capable=False, location=(51.5074, -0.1278)))
    tags = _piexif_tags(out.payload)
    gps = tags.get("GPS", {})
    assert piexif.GPSIFD.GPSLatitude not in gps
    assert piexif.GPSIFD.GPSLongitude not in gps


def test_gps_capable_device_without_location_omits_gps_tags():
    out = write_jpeg(_brief(gps_capable=True, location=None))
    gps = _piexif_tags(out.payload).get("GPS", {})
    assert piexif.GPSIFD.GPSLatitude not in gps


# ---------------------------------------------------------------------------
# Artifact Emitter routing
# ---------------------------------------------------------------------------


def _make_jpeg_registry() -> PersonaRegistry:
    """Minimal registry: one persona with a GPS-capable smartphone."""
    persona = Persona(
        id="p_irene",
        display_name="Irene Adler",
        email_address="irene@bohemia.example",
    )
    smartphone = Device(
        id="d_irene_phone",
        owner_id="p_irene",
        label="irene-iphone",
        profile="jpeg_exif_photo",
        make="Apple",
        model="iPhone 14",
        gps_capable=True,
    )
    camera = Device(
        id="d_irene_camera",
        owner_id="p_irene",
        label="irene-canon",
        profile="jpeg_exif_photo",
        make="Canon",
        model="EOS R6",
        gps_capable=False,
    )
    return PersonaRegistry([persona], [smartphone, camera])


def _make_jpeg_event(device_id: str, location: tuple[float, float] | None) -> Event:
    return Event(
        id="ev_test_1",
        timestamp=_TS,
        actor_id="p_irene",
        device_id=device_id,
        summary="Photo taken at the scene",
        proposition_ids=("prop_1",),
        location=location,
    )


class _StubGateway:
    """Stub LLM gateway — only needed for email profile, not used for jpeg."""

    class stats:
        hits = 0

    def complete(self, *args: Any, **kwargs: Any) -> str:  # pragma: no cover
        return "stub"


def test_emitter_routes_jpeg_event_to_jpeg_profile():
    registry = _make_jpeg_registry()
    ledger = SignalLedger()
    event = _make_jpeg_event("d_irene_phone", location=_LOCATION)
    artifacts = emit_artifacts(
        [event], registry, ledger, gateway=_StubGateway(), disclaimer="SYNTHETIC"
    )
    assert len(artifacts) == 1
    art = artifacts[0]
    assert art.profile == "jpeg_exif_photo"
    assert art.filename.endswith(".jpg")
    img = Image.open(io.BytesIO(art.payload))
    assert img.format == "JPEG"


def test_emitter_jpeg_artifact_has_gps_for_gps_capable_device():
    registry = _make_jpeg_registry()
    ledger = SignalLedger()
    event = _make_jpeg_event("d_irene_phone", location=_LOCATION)
    artifacts = emit_artifacts(
        [event], registry, ledger, gateway=_StubGateway(), disclaimer="SYNTHETIC"
    )
    tags = _piexif_tags(artifacts[0].payload)
    assert piexif.GPSIFD.GPSLatitude in tags["GPS"]


def test_emitter_jpeg_artifact_no_gps_for_camera_device():
    registry = _make_jpeg_registry()
    ledger = SignalLedger()
    event = _make_jpeg_event("d_irene_camera", location=_LOCATION)
    artifacts = emit_artifacts(
        [event], registry, ledger, gateway=_StubGateway(), disclaimer="SYNTHETIC"
    )
    gps = _piexif_tags(artifacts[0].payload).get("GPS", {})
    assert piexif.GPSIFD.GPSLatitude not in gps


def test_emitter_records_jpeg_artifact_in_signal_ledger():
    registry = _make_jpeg_registry()
    ledger = SignalLedger()
    event = _make_jpeg_event("d_irene_phone", location=_LOCATION)
    emit_artifacts([event], registry, ledger, gateway=_StubGateway(), disclaimer="SYNTHETIC")
    entries = ledger.to_json_serialisable()
    assert len(entries) == 1
    assert entries[0]["profile"] == "jpeg_exif_photo"
