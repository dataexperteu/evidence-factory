"""Provenance Catalog — PDF document profile golden round-trip.

Slice 3's correctness backbone: every emitted .pdf must be parseable by an
independent reader (pypdf) and its document metadata must survive a
round-trip with the values supplied in the PdfBrief.
"""

from __future__ import annotations

import hashlib
import io
from datetime import UTC, datetime
from typing import Any

import pypdf
import pytest

from api.pipeline.artifact_emitter import emit_artifacts
from api.pipeline.llm_gateway import LLMGateway
from api.pipeline.persona_registry import PersonaRegistry, default_registry
from api.pipeline.signal_ledger import SignalLedger
from api.pipeline.types import Device, Event, Persona
from api.provenance.pdf_profile import PdfBrief, write_pdf

_DISCLAIMER = "SYNTHETIC EVIDENCE — unit test"

_TS = datetime(2024, 6, 3, 9, 15, 0, tzinfo=UTC)


def _brief(**overrides: Any) -> PdfBrief:
    defaults: dict[str, Any] = dict(
        author_name="Sherlock Holmes",
        title="The Speckled Band — final report",
        body="Watson, this case concerns a most peculiar serpent. My conclusions follow.",
        created_at=_TS,
    )
    defaults.update(overrides)
    return PdfBrief(**defaults)


# ---------------------------------------------------------------------------
# Structural validity
# ---------------------------------------------------------------------------


def test_pdf_parses_cleanly_with_pypdf():
    out = write_pdf(_brief(), disclaimer=_DISCLAIMER)
    reader = pypdf.PdfReader(io.BytesIO(out.payload))
    assert reader.pages  # at least one page


# ---------------------------------------------------------------------------
# Metadata round-trip
# ---------------------------------------------------------------------------


def test_pdf_author_matches_persona_full_name():
    out = write_pdf(_brief(), disclaimer=_DISCLAIMER)
    reader = pypdf.PdfReader(io.BytesIO(out.payload))
    assert reader.metadata.author == "Sherlock Holmes"


def test_pdf_title_matches_brief_title():
    out = write_pdf(_brief(), disclaimer=_DISCLAIMER)
    reader = pypdf.PdfReader(io.BytesIO(out.payload))
    assert reader.metadata.title == "The Speckled Band — final report"


def test_pdf_creation_date_matches_event_timestamp():
    """CreationDate must equal event.timestamp within one-second tolerance."""
    out = write_pdf(_brief(), disclaimer=_DISCLAIMER)
    reader = pypdf.PdfReader(io.BytesIO(out.payload))
    creation = reader.metadata.creation_date
    assert creation is not None
    # Normalise both to UTC for comparison
    expected = _TS.astimezone(UTC).replace(tzinfo=UTC)
    actual = creation.replace(tzinfo=UTC) if creation.tzinfo is None else creation
    delta = abs((actual - expected).total_seconds())
    assert delta < 1, f"CreationDate delta {delta}s exceeds 1-second tolerance"


def test_pdf_mod_date_gte_creation_date():
    out = write_pdf(_brief(), disclaimer=_DISCLAIMER)
    reader = pypdf.PdfReader(io.BytesIO(out.payload))
    meta = reader.metadata
    creation = meta.creation_date
    mod = meta.modification_date
    assert creation is not None
    assert mod is not None
    assert mod >= creation


def test_pdf_producer_is_evidence_factory():
    out = write_pdf(_brief(), disclaimer=_DISCLAIMER)
    reader = pypdf.PdfReader(io.BytesIO(out.payload))
    assert "Evidence Factory" in (reader.metadata.producer or "")


# ---------------------------------------------------------------------------
# SHA-256 stability (golden-file invariant)
# ---------------------------------------------------------------------------


def test_sha256_stability_across_two_writes():
    """Identical inputs must produce identical SHA-256 digests."""
    b = _brief()
    out1 = write_pdf(b, disclaimer=_DISCLAIMER)
    out2 = write_pdf(b, disclaimer=_DISCLAIMER)
    assert out1.sha256 == out2.sha256


def test_sha256_matches_payload_bytes():
    out = write_pdf(_brief(), disclaimer=_DISCLAIMER)
    assert hashlib.sha256(out.payload).hexdigest() == out.sha256


# ---------------------------------------------------------------------------
# Filename safety
# ---------------------------------------------------------------------------


def test_filename_ends_with_pdf():
    out = write_pdf(_brief(), disclaimer=_DISCLAIMER)
    assert out.filename.endswith(".pdf")


def test_filename_is_filesystem_safe():
    out = write_pdf(_brief(title="weird/title:case?<>*"), disclaimer=_DISCLAIMER)
    assert "/" not in out.filename
    assert ":" not in out.filename


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_requires_timezone_aware_created_at():
    bad = _brief(created_at=datetime(2024, 6, 3, 9, 15))  # naive
    with pytest.raises(ValueError, match="timezone-aware"):
        write_pdf(bad, disclaimer=_DISCLAIMER)


# ---------------------------------------------------------------------------
# Registry permission enforcement
# ---------------------------------------------------------------------------


def test_registry_rejects_pdf_on_email_device():
    """The registry must return False when an email device is used for PDF."""
    reg = default_registry()
    holmes = reg.get_persona("p_holmes")
    email_dev = reg.get_device("d_holmes_mail")
    assert not reg.is_permitted(holmes.id, email_dev.id, "pdf")


def test_registry_permits_pdf_on_pdf_device():
    reg = default_registry()
    holmes = reg.get_persona("p_holmes")
    pdf_dev = reg.get_device("d_holmes_workstation")
    assert reg.is_permitted(holmes.id, pdf_dev.id, "pdf")


def test_emitter_raises_when_pdf_device_not_owned_by_persona():
    """emit_artifacts must raise if a persona tries to emit PDF via a device
    owned by a different persona (cross-actor isolation)."""
    holmes = Persona(id="p_h", display_name="Holmes", email_address="h@b.example")
    watson = Persona(id="p_w", display_name="Watson", email_address="w@b.example")
    # watson_pdf_dev is owned by watson, not holmes
    watson_pdf_dev = Device(id="d_w_pdf", owner_id="p_w", label="watson-ws", profile="pdf")
    holmes_email_dev = Device(id="d_h_mail", owner_id="p_h", label="holmes-laptop", profile="email")
    reg = PersonaRegistry([holmes, watson], [holmes_email_dev, watson_pdf_dev])

    # Craft an event that assigns watson's pdf device to holmes (cross-actor)
    event = Event(
        id="ev_x",
        timestamp=_TS,
        actor_id="p_h",
        device_id="d_w_pdf",  # watson's device, not holmes's
        summary="test",
        proposition_ids=("prop_1",),
    )
    ledger = SignalLedger()
    gw = LLMGateway()
    with pytest.raises(ValueError):
        emit_artifacts([event], reg, ledger, gateway=gw, disclaimer=_DISCLAIMER)
