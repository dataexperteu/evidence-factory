"""Persona & Device Registry — permission queries and cross-actor isolation."""

import pytest

from api.pipeline.persona_registry import (
    PersonaRegistry,
    RegistryError,
    default_registry,
    default_registry_with_pdf,
)
from api.pipeline.types import Device, Persona


def test_default_registry_has_four_email_personas():
    reg = default_registry()
    assert len(reg.personas()) == 4
    for persona in reg.personas():
        devs = reg.devices_for(persona.id)
        assert len(devs) == 1
        assert devs[0].profile == "email"


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


# --- Slice-3 PDF permission tests ---


def test_registry_with_pdf_holmes_and_watson_have_pdf_devices():
    reg = default_registry_with_pdf()
    holmes_devs = reg.devices_for("p_holmes")
    watson_devs = reg.devices_for("p_watson")
    assert any(d.profile == "pdf_document" for d in holmes_devs)
    assert any(d.profile == "pdf_document" for d in watson_devs)


def test_registry_with_pdf_hudson_and_lestrade_have_no_pdf_device():
    """Hudson and Lestrade own no PDF-capable device — registry rejects PDF emission."""
    reg = default_registry_with_pdf()
    hudson_devs = reg.devices_for("p_hudson")
    lestrade_devs = reg.devices_for("p_lestrade")
    assert not any(d.profile == "pdf_document" for d in hudson_devs)
    assert not any(d.profile == "pdf_document" for d in lestrade_devs)


def test_is_permitted_pdf_device_for_owner():
    reg = default_registry_with_pdf()
    holmes_pdf_devs = [d for d in reg.devices_for("p_holmes") if d.profile == "pdf_document"]
    assert holmes_pdf_devs, "Holmes should have a PDF device in registry_with_pdf"
    pdf_dev = holmes_pdf_devs[0]
    assert reg.is_permitted("p_holmes", pdf_dev.id, "pdf_document")
    assert not reg.is_permitted("p_watson", pdf_dev.id, "pdf_document")  # cross-actor isolation


def test_registry_rejects_pdf_emission_for_email_only_persona():
    """Emitting a pdf_document artifact through Hudson's email-only device must fail."""
    reg = default_registry_with_pdf()
    hudson_email_devs = [d for d in reg.devices_for("p_hudson") if d.profile == "email"]
    assert hudson_email_devs
    email_dev = hudson_email_devs[0]
    assert not reg.is_permitted("p_hudson", email_dev.id, "pdf_document")


def test_registry_with_pdf_has_four_personas():
    reg = default_registry_with_pdf()
    assert len(reg.personas()) == 4
