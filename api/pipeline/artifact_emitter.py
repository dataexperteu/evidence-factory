"""Artifact Emitter — turns Events into written artifacts.

Routes each event to the correct Provenance Catalog profile based on the
device's declared profile:
  - "email"        → Provenance Catalog Email profile (.eml, RFC822)
  - "pdf_document" → Provenance Catalog PDF profile (.pdf, document metadata)

Records every produced Artifact in the Signal Ledger.
"""

from __future__ import annotations

from ..provenance.email_profile import EmailBrief, write_email
from ..provenance.pdf_profile import PDFBrief, write_pdf
from .llm_gateway import LLMGateway
from .persona_registry import PersonaRegistry
from .signal_ledger import SignalLedger
from .types import Artifact, Device, Event, Persona


def _pick_recipient(registry: PersonaRegistry, sender_id: str) -> tuple[str, str]:
    """Pick a stable non-sender recipient. Two personas always exist in
    the default registry, so this never raises in practice."""
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


def _emit_pdf(
    event: Event,
    persona: Persona,
    device: Device,
    gateway: LLMGateway,
    disclaimer: str,
) -> Artifact:
    body = gateway.complete(
        "artifact_content",
        f"Draft a short document body for: {event.summary}",
        cache_key=f"persona::{persona.id}::pdf",
    )
    title = f"Document — {event.proposition_ids[0]}"
    brief = PDFBrief(
        author=persona.display_name,
        title=title,
        body=body,
        creation_date=event.timestamp,
        mod_date=event.timestamp,
    )
    written = write_pdf(brief, disclaimer=disclaimer)
    return Artifact(
        id=f"art_{event.id}",
        owner_id=persona.id,
        device_id=device.id,
        profile="pdf_document",
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
        if not registry.is_permitted(persona.id, device.id, device.profile):
            raise ValueError(
                f"persona {persona.id} not permitted to emit {device.profile} on device {device.id}"
            )
        if device.profile == "email":
            artifact = _emit_email(event, persona, device, registry, gateway, disclaimer)
        elif device.profile == "pdf_document":
            artifact = _emit_pdf(event, persona, device, gateway, disclaimer)
        else:
            raise ValueError(f"no profile handler for {device.profile!r}")
        ledger.record(artifact)
        artifacts.append(artifact)
    return artifacts
