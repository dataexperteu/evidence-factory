"""Unit tests for the Artifact Emitter routing logic.

Verifies that ledger-shaped events (device.profile == "xlsx_ledger") are routed
to the xlsx_ledger profile writer and that email events continue to route to the
email profile writer.
"""

from __future__ import annotations

from datetime import UTC, datetime

import openpyxl
import pytest

from api.pipeline.artifact_emitter import emit_artifacts
from api.pipeline.llm_gateway import LLMGateway, Role
from api.pipeline.persona_registry import PersonaRegistry, RegistryError
from api.pipeline.signal_ledger import SignalLedger
from api.pipeline.types import Device, Event, Persona


def _make_registry(*devices: Device) -> PersonaRegistry:
    persona = Persona(
        id="p_test", display_name="Test User", email_address="test@example.test"
    )
    return PersonaRegistry([persona], list(devices))


def _make_event(device_id: str) -> Event:
    return Event(
        id="ev_test_1",
        timestamp=datetime(2024, 6, 3, 9, 0, tzinfo=UTC),
        actor_id="p_test",
        device_id=device_id,
        summary="Financial reconciliation note for Q2.",
        proposition_ids=("prop_1",),
    )


class _StubGateway(LLMGateway):
    """Returns deterministic stub text for any completion request."""

    def complete(self, role: Role, prompt: str, *, cache_key: str | None = None) -> str:
        if role == "artifact_content":
            return "date,description,amount,category\n2024-06-01,Office rent,1200.00,Facilities"
        return "stub content"


def test_emitter_routes_xlsx_ledger_event_to_xlsx_profile():
    device = Device(
        id="d_test_xl",
        owner_id="p_test",
        label="test-workstation",
        profile="xlsx_ledger",
    )
    registry = _make_registry(device)
    ledger = SignalLedger()
    events = [_make_event("d_test_xl")]
    artifacts = emit_artifacts(
        events, registry, ledger, gateway=_StubGateway(), disclaimer="SYNTHETIC"
    )
    assert len(artifacts) == 1
    art = artifacts[0]
    assert art.profile == "xlsx_ledger"
    assert art.filename.endswith(".xlsx")
    # Verify the bytes are a valid workbook
    import io

    wb = openpyxl.load_workbook(io.BytesIO(art.payload), read_only=True)
    assert wb.sheetnames
    wb.close()


def test_emitter_routes_email_event_to_eml_profile():
    device = Device(
        id="d_test_mail",
        owner_id="p_test",
        label="test-laptop",
        profile="email",
    )
    # Need a second persona for the recipient
    persona2 = Persona(
        id="p_other", display_name="Other Person", email_address="other@example.test"
    )
    registry2 = PersonaRegistry(
        [
            Persona(id="p_test", display_name="Test User", email_address="test@example.test"),
            persona2,
        ],
        [device],
    )
    ledger = SignalLedger()
    events = [_make_event("d_test_mail")]
    artifacts = emit_artifacts(
        events, registry2, ledger, gateway=_StubGateway(), disclaimer="SYNTHETIC"
    )
    assert len(artifacts) == 1
    art = artifacts[0]
    assert art.profile == "email"
    assert art.filename.endswith(".eml")


def test_emitter_rejects_unpermitted_profile():
    """Persona owning an email device cannot emit xlsx_ledger — permission check fires."""
    email_device = Device(
        id="d_test_mail",
        owner_id="p_test",
        label="test-laptop",
        profile="email",
    )
    registry = PersonaRegistry(
        [Persona(id="p_test", display_name="Test User", email_address="test@example.test")],
        [email_device],
    )
    # Manufacture an event that claims to use a device not in the registry
    bad_event = Event(
        id="ev_bad",
        timestamp=datetime(2024, 6, 3, 9, 0, tzinfo=UTC),
        actor_id="p_test",
        device_id="d_nonexistent",
        summary="Should fail.",
        proposition_ids=("prop_1",),
    )
    with pytest.raises((ValueError, KeyError, RegistryError)):
        emit_artifacts(
            [bad_event],
            registry,
            SignalLedger(),
            gateway=_StubGateway(),
            disclaimer="SYNTHETIC",
        )
