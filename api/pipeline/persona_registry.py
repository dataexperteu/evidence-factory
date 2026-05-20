"""Persona & Device Registry.

Supports both persona-owned devices (email, etc.) and system-owned devices
(system_log_csv). Systems represent automated equipment such as building
access controllers and PBX phone exchanges. Emissions that reference a
persona_id not present in the registry are rejected via is_known_persona().
"""

from __future__ import annotations

from .types import Device, Persona, System


class RegistryError(Exception):
    pass


class PersonaRegistry:
    def __init__(
        self,
        personas: list[Persona],
        devices: list[Device],
        systems: list[System] | None = None,
    ) -> None:
        self._personas: dict[str, Persona] = {p.id: p for p in personas}
        self._systems: dict[str, System] = {s.id: s for s in (systems or [])}
        self._devices: dict[str, Device] = {}
        for d in devices:
            if d.owner_id not in self._personas and d.owner_id not in self._systems:
                raise RegistryError(
                    f"device {d.id} has unknown owner {d.owner_id} (not a persona or system)"
                )
            self._devices[d.id] = d

    # --- persona queries ---

    def personas(self) -> list[Persona]:
        return list(self._personas.values())

    def get_persona(self, persona_id: str) -> Persona:
        if persona_id not in self._personas:
            raise RegistryError(f"unknown persona {persona_id}")
        return self._personas[persona_id]

    def is_known_persona(self, persona_id: str) -> bool:
        return persona_id in self._personas

    def devices_for(self, persona_id: str) -> list[Device]:
        if persona_id not in self._personas:
            raise RegistryError(f"unknown persona {persona_id}")
        return [d for d in self._devices.values() if d.owner_id == persona_id]

    def is_permitted(self, persona_id: str, device_id: str, profile: str) -> bool:
        """True iff persona owns device and device's profile matches."""
        if persona_id not in self._personas or device_id not in self._devices:
            return False
        device = self._devices[device_id]
        return device.owner_id == persona_id and device.profile == profile

    # --- system queries ---

    def systems(self) -> list[System]:
        return list(self._systems.values())

    def get_system(self, system_id: str) -> System:
        if system_id not in self._systems:
            raise RegistryError(f"unknown system {system_id}")
        return self._systems[system_id]

    def devices_for_system(self, system_id: str) -> list[Device]:
        if system_id not in self._systems:
            raise RegistryError(f"unknown system {system_id}")
        return [d for d in self._devices.values() if d.owner_id == system_id]

    # --- generic owner queries (works for both personas and systems) ---

    def get_owner_label(self, owner_id: str) -> str:
        """Return a display label for any owner — persona or system."""
        if owner_id in self._personas:
            return self._personas[owner_id].display_name
        if owner_id in self._systems:
            return self._systems[owner_id].label
        raise RegistryError(f"unknown owner {owner_id}")

    # --- device queries ---

    def get_device(self, device_id: str) -> Device:
        if device_id not in self._devices:
            raise RegistryError(f"unknown device {device_id}")
        return self._devices[device_id]


def default_registry() -> PersonaRegistry:
    """The tracer-bullet cast: four personas + two system devices.

    Persona devices: one email device per persona (four total).
    System devices:
      - building access controller → access_log CSV
      - PBX phone exchange        → cdr CSV
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
            id="p_hudson",
            display_name="Martha Hudson",
            email_address="hudson@baker-street.example",
        ),
        Persona(
            id="p_lestrade",
            display_name="G. Lestrade",
            email_address="lestrade@scotlandyard.example",
        ),
    ]
    systems = [
        System(
            id="sys_bac_221b",
            label="Baker Street Building Controller",
            log_variant="access_log",
        ),
        System(
            id="sys_pbx_baker",
            label="Baker Street PBX",
            log_variant="cdr",
        ),
    ]
    devices = [
        Device(id="d_holmes_mail", owner_id="p_holmes", label="holmes-laptop", profile="email"),
        Device(id="d_watson_mail", owner_id="p_watson", label="watson-laptop", profile="email"),
        Device(id="d_hudson_mail", owner_id="p_hudson", label="hudson-tablet", profile="email"),
        Device(
            id="d_lestrade_mail",
            owner_id="p_lestrade",
            label="lestrade-desktop",
            profile="email",
        ),
        Device(
            id="d_bac_221b",
            owner_id="sys_bac_221b",
            label="bac-controller-221b",
            profile="system_log_csv",
        ),
        Device(
            id="d_pbx_baker",
            owner_id="sys_pbx_baker",
            label="pbx-baker-street",
            profile="system_log_csv",
        ),
    ]
    return PersonaRegistry(personas, devices, systems)
