"""Persona & Device Registry — permission queries and cross-actor isolation.

Merged test suite covering all profiles added by slices 3-7 plus sms.
"""

import pytest

from api.pipeline.persona_registry import (
    PersonaRegistry,
    RegistryError,
    SystemActor,
    default_registry,
)
from api.pipeline.types import Device, Persona

# ---------------------------------------------------------------------------
# Default registry structure
# ---------------------------------------------------------------------------


def test_default_registry_has_six_personas():
    """Merged registry includes original four plus Irene and Mycroft."""
    reg = default_registry()
    assert len(reg.personas()) == 6
    persona_ids = {p.id for p in reg.personas()}
    assert {"p_holmes", "p_watson", "p_hudson", "p_lestrade", "p_irene", "p_mycroft"} == persona_ids


def test_default_registry_original_four_have_email_devices():
    """Slice 1 email closure must be preserved: all four original personas keep email devices."""
    reg = default_registry()
    for pid in ("p_holmes", "p_watson", "p_hudson", "p_lestrade"):
        devs = reg.devices_for(pid)
        assert any(d.profile == "email" for d in devs), f"{pid} must have an email device"


def test_default_registry_four_original_personas_have_pdf_workstations():
    """Slice 3: each original persona has a pdf workstation device."""
    reg = default_registry()
    for pid in ("p_holmes", "p_watson", "p_hudson", "p_lestrade"):
        devs = reg.devices_for(pid)
        assert any(d.profile == "pdf" for d in devs), f"{pid} must have a pdf device"


def test_default_registry_jpeg_devices_present():
    """Slice 6: at least two jpeg-capable device owners in the registry."""
    reg = default_registry()
    jpeg_owners = {
        d.owner_id
        for p in reg.personas()
        for d in reg.devices_for(p.id)
        if d.profile == "jpeg"
    }
    assert len(jpeg_owners) >= 2


def test_default_registry_jpeg_devices_carry_make_model():
    """Slice 6: JPEG devices must have non-empty make and model strings."""
    reg = default_registry()
    jpeg_devices = [
        d
        for p in reg.personas()
        for d in reg.devices_for(p.id)
        if d.profile == "jpeg"
    ]
    for dev in jpeg_devices:
        assert dev.make, f"device {dev.id} missing make"
        assert dev.model, f"device {dev.id} missing model"


def test_default_registry_sms_devices_present():
    """SMS devices must be present for at least two personas."""
    reg = default_registry()
    sms_owners = {
        d.owner_id
        for p in reg.personas()
        for d in reg.devices_for(p.id)
        if d.profile == "sms"
    }
    assert len(sms_owners) >= 2, f"expected ≥2 SMS device owners, got: {sms_owners}"


def test_default_registry_has_two_system_actors():
    reg = default_registry()
    assert reg.is_system_actor("sys_bldg_access")
    assert reg.is_system_actor("sys_pbx")
    assert not reg.is_system_actor("p_holmes")


def test_system_devices_owned_by_system_actors():
    reg = default_registry()
    sys_devs = reg.system_devices()
    assert len(sys_devs) == 2
    for dev in sys_devs:
        assert reg.is_system_actor(dev.owner_id)
        assert dev.profile == "system_log_csv"


# ---------------------------------------------------------------------------
# Permission checks
# ---------------------------------------------------------------------------


def test_is_permitted_email_device():
    reg = default_registry()
    assert reg.is_permitted("p_holmes", "d_holmes_mail", "email")
    assert not reg.is_permitted("p_watson", "d_holmes_mail", "email")  # cross-actor isolation
    assert not reg.is_permitted("p_holmes", "d_holmes_mail", "pdf")  # profile mismatch


def test_is_permitted_pdf_device():
    reg = default_registry()
    assert reg.is_permitted("p_holmes", "d_holmes_workstation", "pdf")
    assert not reg.is_permitted("p_watson", "d_holmes_workstation", "pdf")


def test_is_permitted_system_actor():
    reg = default_registry()
    assert reg.is_permitted("sys_bldg_access", "d_bldg_ctrl", "system_log_csv")
    assert not reg.is_permitted("p_holmes", "d_bldg_ctrl", "system_log_csv")


# ---------------------------------------------------------------------------
# System-actor validation helpers
# ---------------------------------------------------------------------------


def test_validate_persona_ids_accepts_known_ids():
    reg = default_registry()
    known_ids = tuple(reg.persona_ids())
    reg.validate_persona_ids(known_ids)  # must not raise


def test_validate_persona_ids_rejects_unknown_id():
    reg = default_registry()
    with pytest.raises(RegistryError, match="p_ghost"):
        reg.validate_persona_ids(("p_holmes", "p_ghost"))


def test_get_actor_display_name_persona():
    reg = default_registry()
    assert reg.get_actor_display_name("p_holmes") == "Sherlock Holmes"


def test_get_actor_display_name_system_actor():
    reg = default_registry()
    name = reg.get_actor_display_name("sys_bldg_access")
    assert "building" in name.lower()


def test_get_actor_display_name_unknown_raises():
    reg = default_registry()
    with pytest.raises(RegistryError):
        reg.get_actor_display_name("p_ghost")


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_device_with_unknown_owner_rejected():
    persona = Persona(id="p_x", display_name="X", email_address="x@x.example")
    bad_device = Device(id="d_y", owner_id="p_missing", label="y", profile="email")
    with pytest.raises(RegistryError):
        PersonaRegistry([persona], [bad_device])


def test_device_owned_by_system_actor_allowed_when_actor_registered():
    """System actor devices must be accepted when the actor is in the registry."""
    persona = Persona(id="p_x", display_name="X", email_address="x@x.example")
    sa = SystemActor(id="sys_x", label="test-system", log_schema="access_log")
    device = Device(id="d_sys", owner_id="sys_x", label="controller", profile="system_log_csv")
    reg = PersonaRegistry([persona], [device], system_actors=[sa])
    assert reg.is_system_actor("sys_x")
    assert reg.is_permitted("sys_x", "d_sys", "system_log_csv")


def test_unknown_persona_lookup_raises():
    reg = default_registry()
    with pytest.raises(RegistryError):
        reg.devices_for("p_missing")
    with pytest.raises(RegistryError):
        reg.get_persona("p_missing")
    with pytest.raises(RegistryError):
        reg.get_device("d_missing")
