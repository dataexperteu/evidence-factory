"""Unit tests for the Artifact Emitter routing logic.

Verifies that each device profile is dispatched to the correct writer:
  - email   → .eml
  - pdf     → .pdf
  - xlsx_ledger → .xlsx
  - jpeg    → .jpg
  - sms     → .txt
  - system_log_csv → .csv  (system-actor path)
"""

from __future__ import annotations

import io
from datetime import UTC, datetime

import openpyxl
import pytest

from api.pipeline.artifact_emitter import emit_artifacts
from api.pipeline.llm_gateway import LLMGateway, Role
from api.pipeline.persona_registry import PersonaRegistry, SystemActor
from api.pipeline.signal_ledger import SignalLedger
from api.pipeline.types import Device, Event, Persona

_TS = datetime(2024, 6, 3, 9, 0, tzinfo=UTC)


class _StubGateway(LLMGateway):
    """Returns deterministic stub text for any completion request."""

    def complete(self, role: Role, prompt: str, *, cache_key: str | None = None) -> str:
        if role == "artifact_content":
            return "date,description,amount,category\n2024-06-01,Office rent,1200.00,Facilities"
        return "stub event summary"


def _event(device_id: str, actor_id: str = "p_test") -> Event:
    return Event(
        id="ev_test_1",
        timestamp=_TS,
        actor_id=actor_id,
        device_id=device_id,
        summary="Test event summary.",
        proposition_ids=("prop_1",),
    )


def _two_persona_email_registry() -> PersonaRegistry:
    """Two personas, first owns an email device (needed for recipient lookup)."""
    p1 = Persona(id="p_test", display_name="Test User", email_address="test@example.test")
    p2 = Persona(id="p_other", display_name="Other Person", email_address="other@example.test")
    d = Device(id="d_test_mail", owner_id="p_test", label="test-laptop", profile="email")
    return PersonaRegistry([p1, p2], [d])


# ---------------------------------------------------------------------------
# email profile
# ---------------------------------------------------------------------------


def test_emitter_routes_email_event():
    reg = _two_persona_email_registry()
    events = [_event("d_test_mail")]
    artifacts = emit_artifacts(
        events, reg, SignalLedger(), gateway=_StubGateway(), disclaimer="SYN"
    )
    assert len(artifacts) == 1
    assert artifacts[0].profile == "email"
    assert artifacts[0].filename.endswith(".eml")


# ---------------------------------------------------------------------------
# pdf profile
# ---------------------------------------------------------------------------


def test_emitter_routes_pdf_event():
    p = Persona(id="p_test", display_name="Test User", email_address="test@example.test")
    d = Device(id="d_test_ws", owner_id="p_test", label="test-workstation", profile="pdf")
    reg = PersonaRegistry([p], [d])
    artifacts = emit_artifacts(
        [_event("d_test_ws")], reg, SignalLedger(), gateway=_StubGateway(), disclaimer="SYN"
    )
    assert len(artifacts) == 1
    assert artifacts[0].profile == "pdf"
    assert artifacts[0].filename.endswith(".pdf")


# ---------------------------------------------------------------------------
# xlsx_ledger profile
# ---------------------------------------------------------------------------


def test_emitter_routes_xlsx_ledger_event():
    p = Persona(id="p_test", display_name="Test User", email_address="test@example.test")
    d = Device(id="d_test_xl", owner_id="p_test", label="test-workstation", profile="xlsx_ledger")
    reg = PersonaRegistry([p], [d])
    artifacts = emit_artifacts(
        [_event("d_test_xl")], reg, SignalLedger(), gateway=_StubGateway(), disclaimer="SYN"
    )
    assert len(artifacts) == 1
    assert artifacts[0].profile == "xlsx_ledger"
    assert artifacts[0].filename.endswith(".xlsx")
    wb = openpyxl.load_workbook(io.BytesIO(artifacts[0].payload), read_only=True)
    assert wb.sheetnames
    wb.close()


# ---------------------------------------------------------------------------
# jpeg profile
# ---------------------------------------------------------------------------


def test_emitter_routes_jpeg_event():
    p = Persona(id="p_test", display_name="Test User", email_address="test@example.test")
    d = Device(
        id="d_test_cam",
        owner_id="p_test",
        label="test-camera",
        profile="jpeg",
        make="Canon",
        model="EOS R5",
    )
    reg = PersonaRegistry([p], [d])
    artifacts = emit_artifacts(
        [_event("d_test_cam")], reg, SignalLedger(), gateway=_StubGateway(), disclaimer="SYN"
    )
    assert len(artifacts) == 1
    assert artifacts[0].profile == "jpeg"
    assert artifacts[0].filename.endswith(".jpg")
    assert artifacts[0].payload[:2] == b"\xff\xd8"  # JPEG SOI


# ---------------------------------------------------------------------------
# sms profile
# ---------------------------------------------------------------------------


def test_emitter_routes_sms_event():
    p1 = Persona(id="p_test", display_name="Test User", email_address="test@example.test")
    p2 = Persona(id="p_other", display_name="Other Person", email_address="other@example.test")
    d = Device(id="d_test_phone", owner_id="p_test", label="test-phone", profile="sms")
    reg = PersonaRegistry([p1, p2], [d])
    artifacts = emit_artifacts(
        [_event("d_test_phone")], reg, SignalLedger(), gateway=_StubGateway(), disclaimer="SYN"
    )
    assert len(artifacts) == 1
    assert artifacts[0].profile == "sms"
    assert artifacts[0].filename.endswith(".txt")


# ---------------------------------------------------------------------------
# system_log_csv profile (system-actor path)
# ---------------------------------------------------------------------------


def test_emitter_routes_system_log_event():
    p = Persona(id="p_test", display_name="Test User", email_address="test@example.test")
    sa = SystemActor(id="sys_test", label="test-controller", log_schema="access_log")
    d_sys = Device(
        id="d_sys_ctrl", owner_id="sys_test", label="controller", profile="system_log_csv"
    )
    d_mail = Device(id="d_test_mail", owner_id="p_test", label="test-laptop", profile="email")
    reg = PersonaRegistry([p], [d_sys, d_mail], system_actors=[sa])
    sys_event = Event(
        id="ev_sys_1",
        timestamp=_TS,
        actor_id="sys_test",
        device_id="d_sys_ctrl",
        summary="System log event",
        proposition_ids=("prop_1",),
    )
    artifacts = emit_artifacts(
        [sys_event], reg, SignalLedger(), gateway=_StubGateway(), disclaimer="SYN"
    )
    assert len(artifacts) == 1
    assert artifacts[0].profile == "system_log_csv"
    assert artifacts[0].filename.endswith(".csv")


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


def test_emitter_rejects_unknown_device():
    """get_device raises RegistryError for unknown device_id."""
    from api.pipeline.persona_registry import RegistryError

    p = Persona(id="p_test", display_name="Test User", email_address="test@example.test")
    d = Device(id="d_test_mail", owner_id="p_test", label="test-laptop", profile="email")
    reg = PersonaRegistry([p], [d])
    bad_event = Event(
        id="ev_bad",
        timestamp=_TS,
        actor_id="p_test",
        device_id="d_nonexistent",
        summary="Should fail.",
        proposition_ids=("prop_1",),
    )
    with pytest.raises((ValueError, KeyError, RegistryError)):
        emit_artifacts([bad_event], reg, SignalLedger(), gateway=_StubGateway(), disclaimer="SYN")


def test_emitter_rejects_cross_actor_device_use():
    """Persona cannot emit via a device owned by a different persona."""
    p1 = Persona(id="p_test", display_name="Test User", email_address="test@example.test")
    p2 = Persona(id="p_other", display_name="Other Person", email_address="other@example.test")
    d_other = Device(id="d_other_mail", owner_id="p_other", label="other-laptop", profile="email")
    d_self = Device(id="d_test_mail", owner_id="p_test", label="test-laptop", profile="email")
    reg = PersonaRegistry([p1, p2], [d_self, d_other])
    cross_event = Event(
        id="ev_cross",
        timestamp=_TS,
        actor_id="p_test",
        device_id="d_other_mail",  # not owned by p_test
        summary="Cross-actor test.",
        proposition_ids=("prop_1",),
    )
    with pytest.raises(ValueError):
        emit_artifacts([cross_event], reg, SignalLedger(), gateway=_StubGateway(), disclaimer="SYN")
