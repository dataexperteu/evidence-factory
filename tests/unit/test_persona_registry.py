"""Persona & Device Registry — permission queries and cross-actor isolation."""

import pytest

from api.pipeline.persona_registry import PersonaRegistry, RegistryError, default_registry
from api.pipeline.types import Device, Persona


def test_default_registry_email_personas():
    """Slice 1's four email personas are still present and own email devices."""
    reg = default_registry()
    email_persona_ids = {"p_holmes", "p_watson", "p_hudson", "p_lestrade"}
    for pid in email_persona_ids:
        persona = reg.get_persona(pid)
        devs = reg.devices_for(persona.id)
        assert any(d.profile == "email" for d in devs), f"{pid} must have an email device"


def test_default_registry_jpeg_personas():
    """Slice 6 adds at least two JPEG-capable device owners to the registry."""
    reg = default_registry()
    jpeg_device_owners = {
        d.owner_id
        for p in reg.personas()
        for d in reg.devices_for(p.id)
        if d.profile == "jpeg"
    }
    assert len(jpeg_device_owners) >= 2


def test_default_registry_jpeg_devices_carry_make_model():
    """JPEG devices must have non-empty make and model strings."""
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


def test_is_permitted_only_for_owner_device_profile():
    reg = default_registry()
    holmes = reg.get_persona("p_holmes")
    watson = reg.get_persona("p_watson")
    holmes_dev = reg.devices_for(holmes.id)[0]

    assert reg.is_permitted(holmes.id, holmes_dev.id, "email")
    assert not reg.is_permitted(watson.id, holmes_dev.id, "email")  # cross-actor isolation
    assert not reg.is_permitted(holmes.id, holmes_dev.id, "pdf")  # profile mismatch


def test_device_with_unknown_owner_rejected():
    persona = Persona(id="p_x", display_name="X", email_address="x@x.example")
    bad_device = Device(id="d_y", owner_id="p_missing", label="y", profile="email")
    with pytest.raises(RegistryError):
        PersonaRegistry([persona], [bad_device])


def test_unknown_persona_lookup_raises():
    reg = default_registry()
    with pytest.raises(RegistryError):
        reg.devices_for("p_missing")
    with pytest.raises(RegistryError):
        reg.get_persona("p_missing")
    with pytest.raises(RegistryError):
        reg.get_device("d_missing")
