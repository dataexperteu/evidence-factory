"""Event Graph Builder — skeleton.

CanonicalTruth + Persona registry → list[Event].

Slice 1 contract: per proposition, emit one event per persona-device so the
Closure Verifier can find ≥ N owner-distinct corroborators. Timestamps are
spread across a synthetic week to keep the timeline coherent without doing
any real timeline reasoning (later slices add an LLM-backed builder).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .llm_gateway import LLMGateway
from .persona_registry import PersonaRegistry
from .types import CanonicalTruth, Event


def build_events(
    truth: CanonicalTruth,
    registry: PersonaRegistry,
    *,
    gateway: LLMGateway,
    owners_per_proposition: int,
    base_time: datetime | None = None,
) -> list[Event]:
    base = base_time or datetime(2024, 6, 3, 9, 0, 0, tzinfo=UTC)
    personas = registry.personas()
    if owners_per_proposition > len(personas):
        raise ValueError(
            f"owners_per_proposition={owners_per_proposition} exceeds registry size {len(personas)}"
        )

    events: list[Event] = []
    counter = 0
    for prop_index, prop in enumerate(truth.graph.propositions):
        for owner_index in range(owners_per_proposition):
            persona = personas[owner_index]
            devices = registry.devices_for(persona.id)
            if not devices:
                raise ValueError(f"persona {persona.id} owns no devices")
            # Cycle through the persona's devices across owner slots so that
            # the resulting corpus mixes all available profiles (e.g. email and pdf).
            device = devices[owner_index % len(devices)]
            summary = gateway.complete(
                "event_graph",
                f"Describe a one-paragraph event corroborating: {prop.text}",
                cache_key=f"timeline::{prop.id}",
            )
            ts = base + timedelta(days=prop_index, hours=owner_index * 3)
            events.append(
                Event(
                    id=f"ev_{prop.id}_{owner_index + 1}",
                    timestamp=ts,
                    actor_id=persona.id,
                    device_id=device.id,
                    summary=summary,
                    proposition_ids=(prop.id,),
                )
            )
            counter += 1
    return events
