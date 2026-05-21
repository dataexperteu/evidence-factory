"""Persona & Device Registry — permission queries and cross-actor isolation."""

import pytest

from api.pipeline.persona_registry import PersonaRegistry, RegistryError, default_registry
from api.pipeline.types import Device, Persona


def test_default_registry_has_four_personas_with_email_and_pdf_devices():
    """Slice 3: each persona owns one email device and one PDF workstation."""
    reg = default_registry()
    assert len(reg.personas()) == 4
    for persona in reg.personas():
        devs = reg.devices_for(persona.id)
        profiles = {d.profile for d in devs}
        assert "email" in profiles, f"{persona.id} missing email device"
        assert "pdf" in profiles, f"{persona.id} missing pdf device"


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
