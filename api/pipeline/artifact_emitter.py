"""Artifact Emitter — turns Events into written artifacts.

Routes each event to the profile registered on its device:
  - "email"       → .eml via email_profile
  - "xlsx_ledger" → .xlsx via xlsx_profile

For each event the emitter builds a profile-specific brief, writes the
artifact, and records the result in the Signal Ledger.
"""

from __future__ import annotations

from datetime import UTC
from typing import Any

from ..provenance.email_profile import EmailBrief, write_email
from ..provenance.xlsx_profile import XlsxBrief, write_xlsx_ledger
from .llm_gateway import LLMGateway
from .persona_registry import PersonaRegistry
from .signal_ledger import SignalLedger
from .types import Artifact, Event, Persona


def _pick_recipient(registry: PersonaRegistry, sender_id: str) -> tuple[str, str]:
    """Pick a stable non-sender recipient."""
    for persona in registry.personas():
        if persona.id != sender_id:
            return persona.display_name, persona.email_address
    raise ValueError("registry must have at least two personas to emit email")


def _ledger_rows(persona: Persona, event_summary: str, gateway_body: str) -> tuple[dict[str, Any], ...]:
    """Build a small set of ledger rows for an xlsx artifact."""
    from datetime import datetime

    ts = datetime(2024, 1, 1, tzinfo=UTC)  # stable placeholder; real ts comes from Event
    return (
        {"description": event_summary[:80], "amount": 1000.00, "date": ts},
        {"description": gateway_body[:80], "amount": 250.00, "date": ts},
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

        elif device.profile == "xlsx_ledger":
            if not registry.is_permitted(persona.id, device.id, "xlsx_ledger"):
                raise ValueError(
                    f"persona {persona.id} not permitted to emit xlsx_ledger on device {device.id}"
                )
            gateway_body = gateway.complete(
                "artifact_content",
                f"Draft ledger entries matching: {event.summary}",
                cache_key=f"xlsx::{persona.id}",
            )
            rows = _ledger_rows(persona, event.summary, gateway_body)
            # Replace placeholder dates with the event timestamp
            rows = tuple(
                {**r, "date": event.timestamp.astimezone(UTC).replace(tzinfo=None)}
                for r in rows
            )
            brief_xlsx = XlsxBrief(
                creator_name=persona.display_name,
                created=event.timestamp,
                modified=event.timestamp,
                last_modified_by=persona.display_name,
                sheet_name="Evidence Ledger",
                rows=rows,
            )
            written_xlsx = write_xlsx_ledger(brief_xlsx, disclaimer=disclaimer)
            artifact = Artifact(
                id=f"art_{event.id}",
                owner_id=persona.id,
                device_id=device.id,
                profile="xlsx_ledger",
                filename=written_xlsx.filename,
                payload=written_xlsx.payload,
                sha256=written_xlsx.sha256,
                acquisition_time=event.timestamp,
                bound_proposition_ids=event.proposition_ids,
                signal_weight=1.0,
            )

        else:
            raise ValueError(f"unsupported device profile: {device.profile!r}")

        ledger.record(artifact)
        artifacts.append(artifact)
    return artifacts
