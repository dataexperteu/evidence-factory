"""Provenance Catalog — Email .eml profile golden round-trip.

Slice 1's correctness backbone for the Provenance Catalog: every emitted
.eml must be RFC822-parseable by an independent library. We use
`mail-parser` (mailparser) — the library the acceptance criteria suggests.
"""

import email
import hashlib
from datetime import UTC, datetime
from email.header import decode_header, make_header

import mailparser
import pytest

from api.provenance.email_profile import EmailBrief, write_email


def _decoded(header_value: str) -> str:
    return str(make_header(decode_header(header_value)))


def _brief(**overrides) -> EmailBrief:
    defaults = dict(
        sender_name="Sherlock Holmes",
        sender_address="holmes@baker-street.example",
        recipients=(("John Watson", "watson@baker-street.example"),),
        subject="The Speckled Band — meeting notes",
        body="Watson, kindly bring your service revolver. — SH",
        sent_at=datetime(2024, 6, 3, 9, 15, tzinfo=UTC),
    )
    defaults.update(overrides)
    return EmailBrief(**defaults)


def test_eml_round_trips_through_independent_parser():
    out = write_email(_brief(), disclaimer="SYNTHETIC EVIDENCE — demo")
    parsed = mailparser.parse_from_bytes(out.payload)
    assert parsed.subject == "The Speckled Band — meeting notes"
    assert parsed.from_[0][1] == "holmes@baker-street.example"
    assert parsed.to[0][1] == "watson@baker-street.example"
    assert "service revolver" in parsed.body
    # Synthetic-evidence disclaimer header survives the round-trip:
    headers = {k.lower(): v for k, v in parsed.headers.items()}
    assert "synthetic evidence" in headers["x-synthetic-evidence"].lower()


def test_eml_round_trips_through_stdlib_parser():
    """Belt-and-braces: stdlib `email` is the parser most consumers will use."""
    out = write_email(_brief(), disclaimer="d")
    msg = email.message_from_bytes(out.payload)
    assert _decoded(msg["Subject"]) == "The Speckled Band — meeting notes"
    assert msg["Message-ID"] == out.message_id
    assert msg["Date"]  # RFC822 Date present


def test_sha256_matches_payload_bytes():
    out = write_email(_brief(), disclaimer="d")
    assert hashlib.sha256(out.payload).hexdigest() == out.sha256


def test_requires_timezone_aware_sent_at():
    bad = _brief(sent_at=datetime(2024, 6, 3, 9, 15))  # naive
    with pytest.raises(ValueError):
        write_email(bad, disclaimer="d")


def test_requires_at_least_one_recipient():
    bad = _brief(recipients=())
    with pytest.raises(ValueError):
        write_email(bad, disclaimer="d")


def test_filename_is_filesystem_safe():
    out = write_email(_brief(subject="weird/sub:ject?<>*"), disclaimer="d")
    assert "/" not in out.filename
    assert ":" not in out.filename
    assert out.filename.endswith(".eml")
