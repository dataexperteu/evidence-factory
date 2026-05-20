"""Persona & Device Registry — permission queries and cross-actor isolation."""

import pytest

from api.pipeline.persona_registry import PersonaRegistry, RegistryError, default_registry
from api.pipeline.types import Device, Persona


def test_default_registry_has_four_personas_with_email_devices():
    reg = default_registry()
    assert len(reg.personas()) == 4
    for persona in reg.personas():
        devs = reg.devices_for(persona.id)
        assert any(d.profile == "email" for d in devs), (
            f"persona {persona.id} has no email device"
        )


def test_default_registry_xlsx_devices_for_workstation_personas():
    """Holmes and Watson have xlsx_ledger workstation devices (slice 5)."""
    reg = default_registry()
    holmes_devs = reg.devices_for("p_holmes")
    watson_devs = reg.devices_for("p_watson")
    assert any(d.profile == "xlsx_ledger" for d in holmes_devs)
    assert any(d.profile == "xlsx_ledger" for d in watson_devs)
    # Hudson and Lestrade remain email-only
    hudson_devs = reg.devices_for("p_hudson")
    lestrade_devs = reg.devices_for("p_lestrade")
    assert all(d.profile == "email" for d in hudson_devs)
    assert all(d.profile == "email" for d in lestrade_devs)


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
