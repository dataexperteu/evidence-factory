"""Persona & Device Registry — permission queries and cross-actor isolation."""

import pytest

from api.pipeline.persona_registry import PersonaRegistry, RegistryError, default_registry
from api.pipeline.types import Device, Persona


def test_default_registry_has_email_and_sms_personas():
    reg = default_registry()
    all_personas = reg.personas()
    assert len(all_personas) == 6
    profiles = [reg.devices_for(p.id)[0].profile for p in all_personas]
    assert profiles.count("email") == 4
    assert profiles.count("sms") == 2


def test_sms_personas_have_smartphone_devices():
    reg = default_registry()
    sms_personas = [p for p in reg.personas() if reg.devices_for(p.id)[0].profile == "sms"]
    assert len(sms_personas) == 2
    for persona in sms_personas:
        devs = reg.devices_for(persona.id)
        assert len(devs) == 1
        assert devs[0].profile == "sms"
        assert "smartphone" in devs[0].label


def test_is_permitted_only_for_owner_device_profile():
    reg = default_registry()
    holmes = reg.get_persona("p_holmes")
    watson = reg.get_persona("p_watson")
    holmes_dev = reg.devices_for(holmes.id)[0]

    assert reg.is_permitted(holmes.id, holmes_dev.id, "email")
    assert not reg.is_permitted(watson.id, holmes_dev.id, "email")  # cross-actor isolation
    assert not reg.is_permitted(holmes.id, holmes_dev.id, "pdf")  # profile mismatch


def test_sms_device_permitted_for_sms_profile():
    reg = default_registry()
    irene = reg.get_persona("p_irene")
    irene_dev = reg.devices_for(irene.id)[0]
    assert reg.is_permitted(irene.id, irene_dev.id, "sms")
    assert not reg.is_permitted(irene.id, irene_dev.id, "email")  # sms device ≠ email


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
