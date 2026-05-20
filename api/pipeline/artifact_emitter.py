"""Artifact Emitter — turns Events into written artifacts.

Routes each event to the correct profile writer based on the device's profile:
  - "email"            → Email .eml profile
  - "jpeg_exif_photo"  → JPEG + EXIF photo profile

Records every written artifact in the Signal Ledger.
"""

from __future__ import annotations

from ..provenance.email_profile import EmailBrief, write_email
from ..provenance.jpeg_exif_photo_profile import PhotoBrief, write_jpeg
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
    registry: PersonaRegistry,
) -> Artifact:
    persona = registry.get_persona(event.actor_id)
    device = registry.get_device(event.device_id)
    if not registry.is_permitted(persona.id, device.id, "jpeg_exif_photo"):
        raise ValueError(
            f"persona {persona.id} not permitted to emit jpeg_exif_photo on device {device.id}"
        )
    brief = PhotoBrief(
        timestamp=event.timestamp,
        make=device.make,
        model=device.model,
        gps_capable=device.gps_capable,
        location=event.location,
    )
    written = write_jpeg(brief)
    return Artifact(
        id=f"art_{event.id}",
        owner_id=persona.id,
        device_id=device.id,
        profile="jpeg_exif_photo",
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
        device = registry.get_device(event.device_id)
        if device.profile == "email":
            artifact = _emit_email(event, registry, gateway=gateway, disclaimer=disclaimer)
        elif device.profile == "jpeg_exif_photo":
            artifact = _emit_jpeg(event, registry)
        else:
            raise ValueError(f"unsupported device profile: {device.profile!r}")
        ledger.record(artifact)
        artifacts.append(artifact)
    return artifacts
