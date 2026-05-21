"""Persona & Device Registry — permission queries and cross-actor isolation."""

import pytest

from api.pipeline.persona_registry import PersonaRegistry, RegistryError, default_registry
from api.pipeline.types import Device, Persona


def test_default_registry_has_four_personas_with_devices():
    reg = default_registry()
    assert len(reg.personas()) == 4
    for persona in reg.personas():
        devs = reg.devices_for(persona.id)
        assert len(devs) == 1
    # Holmes owns a workstation (xlsx_ledger); the rest own email devices
    assert reg.devices_for("p_holmes")[0].profile == "xlsx_ledger"
    assert reg.devices_for("p_watson")[0].profile == "email"
    assert reg.devices_for("p_hudson")[0].profile == "email"
    assert reg.devices_for("p_lestrade")[0].profile == "email"


def test_is_permitted_only_for_owner_device_profile():
    reg = default_registry()
    holmes = reg.get_persona("p_holmes")
    watson = reg.get_persona("p_watson")
    holmes_dev = reg.devices_for(holmes.id)[0]

    assert reg.is_permitted(holmes.id, holmes_dev.id, "xlsx_ledger")
    assert not reg.is_permitted(watson.id, holmes_dev.id, "xlsx_ledger")  # cross-actor isolation
    assert not reg.is_permitted(holmes.id, holmes_dev.id, "email")  # profile mismatch


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
