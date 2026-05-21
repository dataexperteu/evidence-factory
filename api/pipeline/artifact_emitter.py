"""Artifact Emitter — turns Events into written artifacts.

Routes each event to the appropriate profile writer based on the device's
profile type: email devices produce .eml artifacts; jpeg devices produce
.jpg artifacts with embedded EXIF metadata.
"""

from __future__ import annotations

from ..provenance.email_profile import EmailBrief, write_email
from ..provenance.jpeg_profile import JpegBrief, write_jpeg
from .llm_gateway import LLMGateway
from .persona_registry import PersonaRegistry
from .signal_ledger import SignalLedger
from .types import Artifact, Device, Event, Persona


def _pick_email_recipient(registry: PersonaRegistry, sender_id: str) -> tuple[str, str]:
    """Pick a stable non-sender persona that has an email address."""
    for persona in registry.personas():
        if persona.id != sender_id:
            return persona.display_name, persona.email_address
    raise ValueError("registry must have at least two personas to emit email")


def _emit_email(
    event: Event,
    persona: Persona,
    device: Device,
    registry: PersonaRegistry,
    gateway: LLMGateway,
    disclaimer: str,
) -> Artifact:
    if not registry.is_permitted(persona.id, device.id, "email"):
        raise ValueError(
            f"persona {persona.id} not permitted to emit email on device {device.id}"
        )
    recipient = _pick_email_recipient(registry, persona.id)
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


def _emit_jpeg(
    event: Event,
    persona: Persona,
    device: Device,
    registry: PersonaRegistry,
    gateway: LLMGateway,
    disclaimer: str,
) -> Artifact:
    if not registry.is_permitted(persona.id, device.id, "jpeg"):
        raise ValueError(
            f"persona {persona.id} not permitted to emit jpeg on device {device.id}"
        )
    caption = gateway.complete(
        "artifact_content",
        f"Write a one-sentence photo caption matching: {event.summary}",
        cache_key=f"persona::{persona.id}",
    )
    brief = JpegBrief(
        timestamp=event.timestamp,
        make=device.make,
        model=device.model,
        caption=caption,
    )
    written = write_jpeg(brief, disclaimer=disclaimer)
    return Artifact(
        id=f"art_{event.id}",
        owner_id=persona.id,
        device_id=device.id,
        profile="jpeg",
        filename=written.filename,
        payload=written.payload,
        sha256=written.sha256,
        acquisition_time=event.timestamp,
        bound_proposition_ids=event.proposition_ids,
        signal_weight=1.0,
    )


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
        persona = registry.get_persona(event.actor_id)
        device = registry.get_device(event.device_id)

        if device.profile == "email":
            artifact = _emit_email(event, persona, device, registry, gateway, disclaimer)
        elif device.profile == "jpeg":
            artifact = _emit_jpeg(event, persona, device, registry, gateway, disclaimer)
        else:
            raise ValueError(f"unknown device profile {device.profile!r} for device {device.id}")

        ledger.record(artifact)
        artifacts.append(artifact)
    return artifacts
