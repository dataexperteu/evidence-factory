"""Artifact Emitter — turns Events into written artifacts.

Routing is device-driven: the device's profile field determines which
profile writer is used.  Email devices → .eml; PDF workstation devices → .pdf.
"""

from __future__ import annotations

from ..provenance.email_profile import EmailBrief, write_email
from ..provenance.pdf_profile import PdfBrief, write_pdf
from .llm_gateway import LLMGateway
from .persona_registry import PersonaRegistry
from .signal_ledger import SignalLedger
from .types import Artifact, Event


def _pick_recipient(registry: PersonaRegistry, sender_id: str) -> tuple[str, str]:
    """Pick a stable non-sender recipient for email artifacts."""
    for persona in registry.personas():
        if persona.id != sender_id:
            return persona.display_name, persona.email_address
    raise ValueError("registry must have at least two personas to emit email")


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

        body = gateway.complete(
            "artifact_content",
            f"Draft content matching: {event.summary}",
            cache_key=f"persona::{persona.id}",
        )

        if device.profile == "pdf":
            if not registry.is_permitted(persona.id, device.id, "pdf"):
                raise ValueError(
                    f"persona {persona.id} not permitted to emit pdf on device {device.id}"
                )
            brief = PdfBrief(
                author_name=persona.display_name,
                title=f"Re: {event.proposition_ids[0]} note ({event.id})",
                body=body,
                created_at=event.timestamp,
            )
            written = write_pdf(brief, disclaimer=disclaimer)
            artifact = Artifact(
                id=f"art_{event.id}",
                owner_id=persona.id,
                device_id=device.id,
                profile="pdf",
                filename=written.filename,
                payload=written.payload,
                sha256=written.sha256,
                acquisition_time=event.timestamp,
                bound_proposition_ids=event.proposition_ids,
                signal_weight=1.0,
            )
        else:
            if not registry.is_permitted(persona.id, device.id, "email"):
                raise ValueError(
                    f"persona {persona.id} not permitted to emit email on device {device.id}"
                )
            recipient = _pick_recipient(registry, persona.id)
            brief_email = EmailBrief(
                sender_name=persona.display_name,
                sender_address=persona.email_address,
                recipients=(recipient,),
                subject=f"Re: {event.proposition_ids[0]} note ({event.id})",
                body=body,
                sent_at=event.timestamp,
            )
            written_email = write_email(brief_email, disclaimer=disclaimer)
            artifact = Artifact(
                id=f"art_{event.id}",
                owner_id=persona.id,
                device_id=device.id,
                profile="email",
                filename=written_email.filename,
                payload=written_email.payload,
                sha256=written_email.sha256,
                acquisition_time=event.timestamp,
                bound_proposition_ids=event.proposition_ids,
                signal_weight=1.0,
            )

        ledger.record(artifact)
        artifacts.append(artifact)
    return artifacts
