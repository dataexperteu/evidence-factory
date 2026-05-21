"""Persona & Device Registry.

Slice 1 uses a fixed cast of email-only personas so the deeper "permitted
profile set" enforcement is exercised end-to-end even though only one profile
is implemented. Later slices add device profiles (SMS, PDF, photo, etc.).
"""

from __future__ import annotations

from .types import Device, Persona


class RegistryError(Exception):
    pass


class PersonaRegistry:
    def __init__(self, personas: list[Persona], devices: list[Device]) -> None:
        self._personas: dict[str, Persona] = {p.id: p for p in personas}
        self._devices: dict[str, Device] = {}
        for d in devices:
            if d.owner_id not in self._personas:
                raise RegistryError(f"device {d.id} owned by unknown persona {d.owner_id}")
            self._devices[d.id] = d

    def personas(self) -> list[Persona]:
        return list(self._personas.values())

    def devices_for(self, persona_id: str) -> list[Device]:
        if persona_id not in self._personas:
            raise RegistryError(f"unknown persona {persona_id}")
        return [d for d in self._devices.values() if d.owner_id == persona_id]

    def get_persona(self, persona_id: str) -> Persona:
        if persona_id not in self._personas:
            raise RegistryError(f"unknown persona {persona_id}")
        return self._personas[persona_id]

    def get_device(self, device_id: str) -> Device:
        if device_id not in self._devices:
            raise RegistryError(f"unknown device {device_id}")
        return self._devices[device_id]

    def is_permitted(self, persona_id: str, device_id: str, profile: str) -> bool:
        """True iff persona owns device and device's profile matches."""
        if persona_id not in self._personas or device_id not in self._devices:
            return False
        device = self._devices[device_id]
        return device.owner_id == persona_id and device.profile == profile


def default_registry() -> PersonaRegistry:
    """The tracer-bullet cast: four personas, one email device each.

    Four owners gives the Closure Verifier headroom for owner-distinct
    corroboration thresholds up to 4. Identifiers are stable so the e2e
    smoke test can assert on them.
    """
    personas = [
        Persona(
            id="p_holmes",
            display_name="Sherlock Holmes",
            email_address="holmes@baker-street.example",
        ),
        Persona(
            id="p_watson", display_name="John Watson", email_address="watson@baker-street.example"
        ),
        Persona(
            id="p_hudson", display_name="Martha Hudson", email_address="hudson@baker-street.example"
        ),
        Persona(
            id="p_lestrade",
            display_name="G. Lestrade",
            email_address="lestrade@scotlandyard.example",
        ),
    ]
    devices = [
        Device(id="d_holmes_mail", owner_id="p_holmes", label="holmes-laptop", profile="email"),
        Device(id="d_watson_mail", owner_id="p_watson", label="watson-laptop", profile="email"),
        Device(id="d_hudson_mail", owner_id="p_hudson", label="hudson-tablet", profile="email"),
        Device(
            id="d_lestrade_mail", owner_id="p_lestrade", label="lestrade-desktop", profile="email"
        ),
    ]
    return PersonaRegistry(personas, devices)
