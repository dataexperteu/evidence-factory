"""Persona & Device Registry.

Slice 1 uses a fixed cast of email-only personas so the deeper "permitted
profile set" enforcement is exercised end-to-end even though only one profile
is implemented. Later slices add device profiles (SMS, PDF, photo, etc.).

Slice 7 adds system-owned devices (building access controllers, phone
exchanges) whose emitted system_log_csv artifacts reference persona IDs as
row-level subjects rather than as the emitting owner.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .types import Device, Persona


class RegistryError(Exception):
    pass


@dataclass(frozen=True)
class SystemActor:
    """A non-persona system (e.g. building access controller, PBX) that owns
    log-emitting devices.  ``log_schema`` determines which CSV variant is used."""

    id: str
    label: str
    log_schema: Literal["access_log", "cdr"]


class PersonaRegistry:
    def __init__(
        self,
        personas: list[Persona],
        devices: list[Device],
        system_actors: list[SystemActor] | None = None,
    ) -> None:
        self._personas: dict[str, Persona] = {p.id: p for p in personas}
        self._system_actors: dict[str, SystemActor] = {sa.id: sa for sa in (system_actors or [])}
        self._devices: dict[str, Device] = {}
        for d in devices:
            if d.owner_id not in self._personas and d.owner_id not in self._system_actors:
                raise RegistryError(f"device {d.id} owned by unknown actor {d.owner_id}")
            self._devices[d.id] = d

    # ------------------------------------------------------------------
    # Persona queries
    # ------------------------------------------------------------------

    def personas(self) -> list[Persona]:
        return list(self._personas.values())

    def persona_ids(self) -> set[str]:
        return set(self._personas.keys())

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

    def is_permitted(self, actor_id: str, device_id: str, profile: str) -> bool:
        """True iff actor owns device and device's profile matches."""
        is_known = actor_id in self._personas or actor_id in self._system_actors
        if not is_known or device_id not in self._devices:
            return False
        device = self._devices[device_id]
        return device.owner_id == actor_id and device.profile == profile

    # ------------------------------------------------------------------
    # System-actor queries
    # ------------------------------------------------------------------

    def validate_persona_ids(self, persona_ids: tuple[str, ...]) -> None:
        """Raise RegistryError if any persona_id is not in the registry.

        Called by the Artifact Emitter before writing a system log so that
        an emission with an unknown persona_id is rejected by construction.
        """
        unknown = set(persona_ids) - self._personas.keys()
        if unknown:
            raise RegistryError(
                f"system log references persona ids not in registry: {sorted(unknown)}"
            )

    def is_system_actor(self, actor_id: str) -> bool:
        return actor_id in self._system_actors

    def get_system_actor(self, actor_id: str) -> SystemActor:
        if actor_id not in self._system_actors:
            raise RegistryError(f"unknown system actor {actor_id}")
        return self._system_actors[actor_id]

    def system_devices(self) -> list[Device]:
        return [d for d in self._devices.values() if d.owner_id in self._system_actors]

    # ------------------------------------------------------------------
    # Unified actor helpers (used by packager for corpus paths)
    # ------------------------------------------------------------------

    def get_actor_display_name(self, actor_id: str) -> str:
        """Return a display label for any actor (persona or system)."""
        if actor_id in self._personas:
            return self._personas[actor_id].display_name
        if actor_id in self._system_actors:
            return self._system_actors[actor_id].label
        raise RegistryError(f"unknown actor {actor_id}")


def default_registry() -> PersonaRegistry:
    """Merged cast: six personas, two system actors, all profile devices.

    Persona roster (index order matters for event_graph cycling):
      0  p_holmes  — email / pdf / sms
      1  p_watson  — email / pdf / sms
      2  p_irene   — jpeg phone
      3  p_mycroft — xlsx_ledger workstation
      4  p_hudson  — email / sms / pdf   (sms at index 1 → slot 4 hits sms)
      5  p_lestrade — email / pdf

    With owners_per_proposition=5 the cycling produces one artifact per
    slot: email(0), pdf(1), jpeg(2), xlsx_ledger(3), sms(4).
    System actors add system_log_csv, covering all six profiles.
    """
    personas = [
        Persona(
            id="p_holmes",
            display_name="Sherlock Holmes",
            email_address="holmes@baker-street.example",
        ),
        Persona(
            id="p_watson",
            display_name="John Watson",
            email_address="watson@baker-street.example",
        ),
        Persona(
            id="p_irene",
            display_name="Irene Adler",
            email_address="irene@adler.example",
        ),
        Persona(
            id="p_mycroft",
            display_name="Mycroft Holmes",
            email_address="mycroft@diogenes.example",
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
    system_actors = [
        SystemActor(
            id="sys_bldg_access",
            label="building-access-controller",
            log_schema="access_log",
        ),
        SystemActor(
            id="sys_pbx",
            label="phone-exchange",
            log_schema="cdr",
        ),
    ]
    devices = [
        # Holmes — email + pdf + sms
        Device(id="d_holmes_mail", owner_id="p_holmes", label="holmes-laptop", profile="email"),
        Device(
            id="d_holmes_workstation",
            owner_id="p_holmes",
            label="holmes-workstation",
            profile="pdf",
        ),
        Device(
            id="d_holmes_phone",
            owner_id="p_holmes",
            label="holmes-phone",
            profile="sms",
        ),
        # Watson — email + pdf + sms
        Device(id="d_watson_mail", owner_id="p_watson", label="watson-laptop", profile="email"),
        Device(
            id="d_watson_workstation",
            owner_id="p_watson",
            label="watson-workstation",
            profile="pdf",
        ),
        Device(
            id="d_watson_phone",
            owner_id="p_watson",
            label="watson-phone",
            profile="sms",
        ),
        # Irene — jpeg phone
        Device(
            id="d_irene_phone",
            owner_id="p_irene",
            label="irene-phone",
            profile="jpeg",
            make="Apple",
            model="iPhone 15 Pro",
            gps_capable=True,
        ),
        # Mycroft — xlsx_ledger workstation (slot 3 in owners_per_proposition=5 cycling)
        Device(
            id="d_mycroft_workstation",
            owner_id="p_mycroft",
            label="mycroft-workstation",
            profile="xlsx_ledger",
        ),
        # Hudson — email + sms + pdf  (sms at index 1 so devices[4%3=1]=sms in slot 4)
        Device(id="d_hudson_mail", owner_id="p_hudson", label="hudson-tablet", profile="email"),
        Device(id="d_hudson_phone", owner_id="p_hudson", label="hudson-phone", profile="sms"),
        Device(
            id="d_hudson_workstation",
            owner_id="p_hudson",
            label="hudson-workstation",
            profile="pdf",
        ),
        # Lestrade — email + pdf
        Device(
            id="d_lestrade_mail",
            owner_id="p_lestrade",
            label="lestrade-desktop",
            profile="email",
        ),
        Device(
            id="d_lestrade_workstation",
            owner_id="p_lestrade",
            label="lestrade-workstation",
            profile="pdf",
        ),
        # System devices
        Device(
            id="d_bldg_ctrl",
            owner_id="sys_bldg_access",
            label="building-controller",
            profile="system_log_csv",
        ),
        Device(
            id="d_pbx",
            owner_id="sys_pbx",
            label="phone-exchange",
            profile="system_log_csv",
        ),
    ]
    return PersonaRegistry(personas, devices, system_actors=system_actors)
