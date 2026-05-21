"""Artifact Emitter — turns Events into written artifacts.

Persona-owned events → email (.eml) artifacts.
System-actor events  → system_log_csv (.csv) artifacts.

Each emitted artifact is recorded in the Signal Ledger so the Closure
Verifier can confirm owner-distinct corroboration for every proposition.
"""

from __future__ import annotations

from ..provenance.email_profile import EmailBrief, write_email
from ..provenance.system_log_csv_profile import SystemLogBrief, write_system_log_csv
from .llm_gateway import LLMGateway
from .persona_registry import PersonaRegistry
from .signal_ledger import SignalLedger
from .types import Artifact, Event


def _pick_recipient(registry: PersonaRegistry, sender_id: str) -> tuple[str, str]:
    """Pick a stable non-sender recipient. Two personas always exist in
    the default registry, so this never raises in practice."""
    for persona in registry.personas():
        if persona.id != sender_id:
            return persona.display_name, persona.email_address
    raise ValueError("registry must have at least two personas to emit email")


def _emit_email(
    event: Event,
    registry: PersonaRegistry,
    ledger: SignalLedger,
    *,
    gateway: LLMGateway,
    disclaimer: str,
) -> Artifact:
    persona = registry.get_persona(event.actor_id)
    device = registry.get_device(event.device_id)
    if not registry.is_permitted(persona.id, device.id, "email"):
        raise ValueError(
            f"persona {persona.id} not permitted to emit email on device {device.id}"
        )
    recipient = _pick_recipient(registry, persona.id)
    body = gateway.complete(
        "artifact_content",
        f"Draft a short email body matching: {event.summary}",
        cache_key=f"persona::{persona.id}",
    )
    brief = EmailBrief(
        sender_name=persona.display_name,
        sender_address=persona.email_address,
        recipients=(recipient,),
        subject=f"Re: {event.proposition_ids[0]} note ({event.id})",
        body=body,
        sent_at=event.timestamp,
    )
    written = write_email(brief, disclaimer=disclaimer)
    artifact = Artifact(
        id=f"art_{event.id}",
        owner_id=persona.id,
        device_id=device.id,
        profile="email",
        filename=written.filename,
        payload=written.payload,
        sha256=written.sha256,
        acquisition_time=event.timestamp,
        bound_proposition_ids=event.proposition_ids,
        signal_weight=1.0,
    )
    ledger.record(artifact)
    return artifact


def _emit_system_log(
    event: Event,
    registry: PersonaRegistry,
    ledger: SignalLedger,
    *,
    disclaimer: str,
) -> Artifact:
    system_actor = registry.get_system_actor(event.actor_id)
    device = registry.get_device(event.device_id)
    if not registry.is_permitted(system_actor.id, device.id, "system_log_csv"):
        raise ValueError(
            f"system actor {system_actor.id} not permitted to emit system_log_csv "
            f"on device {device.id}"
        )
    # Row subjects: all personas in the registry; reject unknown IDs at write time
    persona_ids = tuple(sorted(registry.persona_ids()))
    if not persona_ids:
        raise ValueError("registry must contain at least one persona for system log rows")
    brief = SystemLogBrief(
        schema=system_actor.log_schema,
        device_id=device.id,
        event_id=event.id,
        event_timestamp=event.timestamp,
        persona_ids=persona_ids,
    )
    written = write_system_log_csv(brief, disclaimer=disclaimer)
    artifact = Artifact(
        id=f"art_{event.id}",
        owner_id=system_actor.id,
        device_id=device.id,
        profile="system_log_csv",
        filename=written.filename,
        payload=written.payload,
        sha256=written.sha256,
        acquisition_time=event.timestamp,
        bound_proposition_ids=event.proposition_ids,
        signal_weight=1.0,
    )
    ledger.record(artifact)
    return artifact


def emit_artifacts(
    events: list[Event],
    registry: PersonaRegistry,
    ledger: SignalLedger,
    *,
    gateway: LLMGateway,
    disclaimer: str,
) -> list[Artifact]:
    artifacts: list[Artifact] = []
    for event in events:
        if registry.is_system_actor(event.actor_id):
            artifact = _emit_system_log(event, registry, ledger, disclaimer=disclaimer)
        else:
            artifact = _emit_email(event, registry, ledger, gateway=gateway, disclaimer=disclaimer)
        artifacts.append(artifact)
    return artifacts
