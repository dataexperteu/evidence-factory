"""Device.profiles tuple — acceptance criteria tests for slice 21.

Verifies:
- Device.profiles is a tuple of one or more profile literals
- is_permitted checks membership in device.profiles
- Default registry devices carry realistic multi-profile tuples
- All dispatch switches work correctly for multi-profile devices
"""

from __future__ import annotations

from api.pipeline.persona_registry import (
    PersonaRegistry,
    default_registry,
)
from api.pipeline.types import Device, Persona

# ---------------------------------------------------------------------------
# Device.profiles schema
# ---------------------------------------------------------------------------


def test_device_profiles_is_tuple():
    p = Persona(id="p_x", display_name="X", email_address="x@x.example")
    d = Device(id="d_x", owner_id="p_x", label="x-laptop", profiles=("email", "pdf"))
    reg = PersonaRegistry([p], [d])
    device = reg.get_device("d_x")
    assert isinstance(device.profiles, tuple)
    assert len(device.profiles) >= 1


def test_device_has_no_profile_singular():
    """Device.profile (singular) no longer exists."""
    d = Device(id="d_x", owner_id="p_x", label="x-laptop", profiles=("email",))
    assert not hasattr(d, "profile")
    assert hasattr(d, "profiles")


# ---------------------------------------------------------------------------
# is_permitted — membership check
# ---------------------------------------------------------------------------


def test_is_permitted_checks_membership_in_profiles():
    """is_permitted returns True for any profile in device.profiles."""
    p = Persona(id="p_x", display_name="X", email_address="x@x.example")
    d = Device(id="d_x", owner_id="p_x", label="x-laptop", profiles=("email", "pdf"))
    reg = PersonaRegistry([p], [d])
    assert reg.is_permitted("p_x", "d_x", "email")
    assert reg.is_permitted("p_x", "d_x", "pdf")
    assert not reg.is_permitted("p_x", "d_x", "sms")


def test_is_permitted_cross_actor_still_blocked():
    """Cross-actor isolation still holds with multi-profile devices."""
    p1 = Persona(id="p_x", display_name="X", email_address="x@x.example")
    p2 = Persona(id="p_y", display_name="Y", email_address="y@x.example")
    d = Device(id="d_x", owner_id="p_x", label="x-laptop", profiles=("email", "pdf"))
    reg = PersonaRegistry([p1, p2], [d])
    assert reg.is_permitted("p_x", "d_x", "email")
    assert not reg.is_permitted("p_y", "d_x", "email")
    assert not reg.is_permitted("p_y", "d_x", "pdf")


# ---------------------------------------------------------------------------
# Default registry — realistic multi-profile tuples
# ---------------------------------------------------------------------------


def test_default_registry_laptops_have_email_and_pdf():
    """Laptops and tablets carry ("email", "pdf")."""
    reg = default_registry()
    laptop_ids = ["d_holmes_mail", "d_watson_mail", "d_hudson_mail", "d_lestrade_mail"]
    for device_id in laptop_ids:
        device = reg.get_device(device_id)
        assert "email" in device.profiles, f"{device_id} missing email"
        assert "pdf" in device.profiles, f"{device_id} missing pdf"


def test_default_registry_workstations_have_all_three_profiles():
    """Workstations carry ("pdf", "email", "xlsx_ledger")."""
    reg = default_registry()
    ws_ids = [
        "d_holmes_workstation",
        "d_watson_workstation",
        "d_mycroft_workstation",
        "d_hudson_workstation",
        "d_lestrade_workstation",
    ]
    for device_id in ws_ids:
        device = reg.get_device(device_id)
        assert "pdf" in device.profiles, f"{device_id} missing pdf"
        assert "email" in device.profiles, f"{device_id} missing email"
        assert "xlsx_ledger" in device.profiles, f"{device_id} missing xlsx_ledger"


def test_default_registry_phones_have_sms_and_jpeg():
    """Phones carry ("sms", "jpeg")."""
    reg = default_registry()
    phone_ids = [
        "d_holmes_phone",
        "d_watson_phone",
        "d_irene_phone",
        "d_hudson_phone",
    ]
    for device_id in phone_ids:
        device = reg.get_device(device_id)
        assert "sms" in device.profiles, f"{device_id} missing sms"
        assert "jpeg" in device.profiles, f"{device_id} missing jpeg"


def test_default_registry_system_devices_unchanged():
    """System devices retain single-profile ("system_log_csv",)."""
    reg = default_registry()
    for device in reg.system_devices():
        assert device.profiles == ("system_log_csv",), (
            f"{device.id} expected ('system_log_csv',), got {device.profiles}"
        )


def test_default_registry_primary_profiles_unchanged():
    """profiles[0] matches what profile used to be, preserving event-graph dispatch."""
    reg = default_registry()
    expected_primary = {
        "d_holmes_mail": "email",
        "d_holmes_workstation": "pdf",
        "d_holmes_phone": "sms",
        "d_watson_mail": "email",
        "d_watson_workstation": "pdf",
        "d_watson_phone": "sms",
        "d_irene_phone": "sms",
        "d_mycroft_workstation": "pdf",
        "d_hudson_mail": "email",
        "d_hudson_phone": "sms",
        "d_hudson_workstation": "pdf",
        "d_lestrade_mail": "email",
        "d_lestrade_workstation": "pdf",
        "d_bldg_ctrl": "system_log_csv",
        "d_pbx": "system_log_csv",
    }
    for device_id, expected in expected_primary.items():
        device = reg.get_device(device_id)
        assert device.profiles[0] == expected, (
            f"{device_id}: expected primary profile {expected!r}, got {device.profiles[0]!r}"
        )
