"""Artifact Emitter — turns Events into written artifacts.

For each event, delegates to the appropriate Provenance Catalog profile
(email or sms) based on the event's device, then records the resulting
Artifact in the Signal Ledger.
"""

from __future__ import annotations

from datetime import timedelta

from ..provenance.email_profile import EmailBrief, write_email
from ..provenance.sms_profile import SmsBrief, SmsMessage, write_sms_csv
from .llm_gateway import LLMGateway
from .persona_registry import PersonaRegistry
from .signal_ledger import SignalLedger
from .types import Artifact, Event


def _pick_other(registry: PersonaRegistry, sender_id: str) -> tuple[str, str]:
    """Return (display_name, email_address) of a stable non-sender persona."""
    for persona in registry.personas():
        if persona.id != sender_id:
            return persona.display_name, persona.email_address
    raise ValueError("registry must have at least two personas")


def _emit_email(
    event: Event,
    registry: PersonaRegistry,
    *,
    gateway: LLMGateway,
    disclaimer: str,
) -> Artifact:
    persona = registry.get_persona(event.actor_id)
    device = registry.get_device(event.device_id)
    if not registry.is_permitted(persona.id, device.id, "email"):
        raise ValueError(f"persona {persona.id} not permitted to emit email on device {device.id}")
    recipient_name, recipient_addr = _pick_other(registry, persona.id)
    body = gateway.complete(
        "artifact_content",
        f"Draft a short email body matching: {event.summary}",
        cache_key=f"persona::{persona.id}",
    )
    brief = EmailBrief(
        sender_name=persona.display_name,
        sender_address=persona.email_address,
        recipients=((recipient_name, recipient_addr),),
        subject=f"Re: {event.proposition_ids[0]} note ({event.id})",
        body=body,
        sent_at=event.timestamp,
    )
    written = write_email(brief, disclaimer=disclaimer)
    return Artifact(
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


def _emit_sms(
    event: Event,
    registry: PersonaRegistry,
    *,
    gateway: LLMGateway,
    disclaimer: str,
) -> Artifact:
    persona = registry.get_persona(event.actor_id)
    device = registry.get_device(event.device_id)
    if not registry.is_permitted(persona.id, device.id, "sms"):
        raise ValueError(f"persona {persona.id} not permitted to emit sms on device {device.id}")
    recipient_name, _ = _pick_other(registry, persona.id)
    body = gateway.complete(
        "artifact_content",
        f"Draft a short SMS message matching: {event.summary}",
        cache_key=f"persona::{persona.id}",
    )
    window_end = event.timestamp + timedelta(minutes=5)
    brief = SmsBrief(
        thread_id=event.id,
        participants=(persona.display_name, recipient_name),
        messages=(
            SmsMessage(
                sender=persona.display_name,
                recipient=recipient_name,
                timestamp_iso=event.timestamp.isoformat(),
                body=body,
            ),
        ),
        window_start=event.timestamp,
        window_end=window_end,
    )
    written = write_sms_csv(brief, owner=persona.display_name, disclaimer=disclaimer)
    return Artifact(
        id=f"art_{event.id}",
        owner_id=persona.id,
        device_id=device.id,
        profile="sms",
        filename=written.filename,
        payload=written.payload,
        sha256=written.sha256,
        acquisition_time=event.timestamp,
        bound_proposition_ids=event.proposition_ids,
        signal_weight=1.0,
    )


_DISPATCHERS = {
    "email": _emit_email,
    "sms": _emit_sms,
}


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
        device = registry.get_device(event.device_id)
        dispatcher = _DISPATCHERS.get(device.profile)
        if dispatcher is None:
            raise ValueError(f"no emitter registered for profile '{device.profile}'")
        artifact = dispatcher(event, registry, gateway=gateway, disclaimer=disclaimer)
        ledger.record(artifact)
        artifacts.append(artifact)
    return artifacts
