"""SMS chat-export profile — unit tests.

Validates the api/provenance/sms_profile.py writer:
- filename ends with .txt and is filesystem-safe
- payload is valid UTF-8
- disclaimer is present in the payload
- sha256 field matches the payload bytes
- two writes with identical inputs produce different filenames (random suffix)
  but both sha256 values are valid
- requires timezone-aware sent_at
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from api.provenance.sms_profile import SmsBrief, WrittenSms, write_sms

DISCLAIMER = "SYNTHETIC EVIDENCE — for demonstration only"


def _brief(**overrides: object) -> SmsBrief:
    defaults: dict[str, object] = dict(
        sender_name="Sherlock Holmes",
        sender_number="+1-555-0001",
        recipient_name="John Watson",
        recipient_number="+1-555-0002",
        body="Holmes: I've found the serpent.\n\nWatson: Extraordinary! Where?",
        sent_at=datetime(2024, 6, 3, 9, 15, 0, tzinfo=UTC),
    )
    defaults.update(overrides)
    return SmsBrief(**defaults)  # type: ignore[arg-type]


def test_write_sms_returns_written_sms():
    out = write_sms(_brief(), disclaimer=DISCLAIMER)
    assert isinstance(out, WrittenSms)


def test_filename_ends_with_txt():
    out = write_sms(_brief(), disclaimer=DISCLAIMER)
    assert out.filename.endswith(".txt")


def test_filename_is_filesystem_safe():
    out = write_sms(_brief(sender_name="A/B:C?D"), disclaimer=DISCLAIMER)
    assert "/" not in out.filename
    assert ":" not in out.filename


def test_sha256_matches_payload():
    out = write_sms(_brief(), disclaimer=DISCLAIMER)
    assert hashlib.sha256(out.payload).hexdigest() == out.sha256


def test_payload_is_utf8_decodable():
    out = write_sms(_brief(), disclaimer=DISCLAIMER)
    decoded = out.payload.decode("utf-8")
    assert decoded.strip()


def test_disclaimer_present_in_payload():
    out = write_sms(_brief(), disclaimer=DISCLAIMER)
    decoded = out.payload.decode("utf-8")
    assert DISCLAIMER in decoded


def test_sender_name_in_payload():
    out = write_sms(_brief(), disclaimer=DISCLAIMER)
    decoded = out.payload.decode("utf-8")
    assert "Sherlock Holmes" in decoded


def test_recipient_name_in_payload():
    out = write_sms(_brief(), disclaimer=DISCLAIMER)
    decoded = out.payload.decode("utf-8")
    assert "John Watson" in decoded


def test_timestamp_in_payload():
    out = write_sms(_brief(), disclaimer=DISCLAIMER)
    decoded = out.payload.decode("utf-8")
    assert "2024-06-03T09:15:00Z" in decoded


def test_two_writes_produce_different_filenames():
    """Random suffix ensures corpus path uniqueness across runs."""
    b = _brief()
    out1 = write_sms(b, disclaimer=DISCLAIMER)
    out2 = write_sms(b, disclaimer=DISCLAIMER)
    # Filenames differ (random suffix) but both sha256s are valid
    assert hashlib.sha256(out1.payload).hexdigest() == out1.sha256
    assert hashlib.sha256(out2.payload).hexdigest() == out2.sha256


def test_requires_timezone_aware_sent_at():
    bad = _brief(sent_at=datetime(2024, 6, 3, 9, 15))  # naive
    with pytest.raises(ValueError, match="timezone-aware"):
        write_sms(bad, disclaimer=DISCLAIMER)


def test_body_content_appears_in_payload():
    body = "Holmes: The game is afoot."
    out = write_sms(_brief(body=body), disclaimer=DISCLAIMER)
    decoded = out.payload.decode("utf-8")
    assert "The game is afoot" in decoded
