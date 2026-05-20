"""Event Graph Builder.

CanonicalTruth + Persona registry → list[Event].

For each proposition this builder emits:
  - one email event per persona owner (up to owners_per_proposition)
  - one access_log event per access-controller system device
  - one CDR event per PBX system device

System log events carry subject_ids drawn from the first two personas so
the Artifact Emitter can populate CSV rows referencing known registry members.
Timestamps are spread deterministically to keep the timeline coherent.
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

    # Two personas referenced as subjects in system log rows.
    log_subjects = tuple(p.id for p in personas[:2])

    events: list[Event] = []
    for prop_index, prop in enumerate(truth.graph.propositions):
        prop_base = base + timedelta(days=prop_index)

        # Email events — one per persona owner.
        for owner_index in range(owners_per_proposition):
            persona = personas[owner_index]
            devices = registry.devices_for(persona.id)
            if not devices:
                raise ValueError(f"persona {persona.id} owns no devices")
            device = devices[0]
            summary = gateway.complete(
                "event_graph",
                f"Describe a one-paragraph event corroborating: {prop.text}",
                cache_key=f"timeline::{prop.id}",
            )
            ts = prop_base + timedelta(hours=owner_index * 3)
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

        # System log events — one per system device.
        for system in registry.systems():
            sys_devices = registry.devices_for_system(system.id)
            for dev_index, sys_dev in enumerate(sys_devices):
                # Offset system events to after email events for the day.
                ts = prop_base + timedelta(hours=owners_per_proposition * 3 + dev_index)
                events.append(
                    Event(
                        id=f"ev_{prop.id}_sys_{system.id}_{dev_index}",
                        timestamp=ts,
                        actor_id=system.id,
                        device_id=sys_dev.id,
                        summary=f"System log covering proposition: {prop.text[:60]}",
                        proposition_ids=(prop.id,),
                        subject_ids=log_subjects,
                    )
                )

    return events
