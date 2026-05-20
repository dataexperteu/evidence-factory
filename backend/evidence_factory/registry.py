from __future__ import annotations

import random
from collections.abc import Sequence

from .models import ArtifactProfile, Device, Persona


class PersonaRegistry:
    def __init__(self, personas: Sequence[Persona]) -> None:
        self._personas = {p.name: p for p in personas}
        self._devices: dict[str, dict[str, Device]] = {}
        for p in personas:
            self._devices[p.name] = {d.device_id: d for d in p.devices}

    def is_permitted(self, owner: str, device_id: str, profile: ArtifactProfile) -> bool:
        if owner not in self._devices:
            return False
        if device_id not in self._devices[owner]:
            return False
        return profile in self._devices[owner][device_id].permitted_profiles

    def persona_names(self) -> list[str]:
        return list(self._personas.keys())

    def devices_for(self, owner: str) -> list[Device]:
        return list(self._devices.get(owner, {}).values())

    def all_permitted_triples(self) -> list[tuple[str, str, ArtifactProfile]]:
        triples: list[tuple[str, str, ArtifactProfile]] = []
        for persona_name, devices in self._devices.items():
            for device_id, device in devices.items():
                for profile in device.permitted_profiles:
                    triples.append((persona_name, device_id, profile))
        return triples

    def permitted_triples_for_profile(
        self, profile: ArtifactProfile
    ) -> list[tuple[str, str, ArtifactProfile]]:
        return [(o, d, p) for o, d, p in self.all_permitted_triples() if p == profile]

    def random_permitted_triple(self) -> tuple[str, str, ArtifactProfile]:
        triples = self.all_permitted_triples()
        if not triples:
            raise ValueError("No permitted triples in registry")
        return random.choice(triples)
