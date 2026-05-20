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
    """Full cast: four email personas + two SMS (smartphone) personas.

    Email personas (indices 0-3) are the slice-1 tracer-bullet cast.
    SMS personas (indices 4-5) are added in slice 4 and give the pipeline
    SMS-capable devices for corroboration.  Identifiers are stable so the
    e2e smoke tests can assert on them.
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
        Persona(
            id="p_irene",
            display_name="Irene Adler",
            email_address="irene@adler.example",
        ),
        Persona(
            id="p_moriarty",
            display_name="Prof. Moriarty",
            email_address="moriarty@crimefac.example",
        ),
    ]
    devices = [
        Device(id="d_holmes_mail", owner_id="p_holmes", label="holmes-laptop", profile="email"),
        Device(id="d_watson_mail", owner_id="p_watson", label="watson-laptop", profile="email"),
        Device(id="d_hudson_mail", owner_id="p_hudson", label="hudson-tablet", profile="email"),
        Device(
            id="d_lestrade_mail", owner_id="p_lestrade", label="lestrade-desktop", profile="email"
        ),
        Device(
            id="d_irene_sms", owner_id="p_irene", label="irene-smartphone", profile="sms"
        ),
        Device(
            id="d_moriarty_sms", owner_id="p_moriarty", label="moriarty-smartphone", profile="sms"
        ),
    ]
    return PersonaRegistry(personas, devices)
