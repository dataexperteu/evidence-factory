"""Artifact Emitter — turns Events into written artifacts.

Dispatches each event to the appropriate profile writer based on the owning
device's profile field.  Currently supports "email" and "xlsx_ledger".
"""

from __future__ import annotations

import csv
import io

from ..provenance.email_profile import EmailBrief, write_email
from ..provenance.xlsx_ledger_profile import XlsxLedgerBrief, write_xlsx_ledger
from .llm_gateway import LLMGateway
from .persona_registry import PersonaRegistry
from .signal_ledger import SignalLedger
from .types import Artifact, Event


def _pick_recipient(registry: PersonaRegistry, sender_id: str) -> tuple[str, str]:
    """Pick a stable non-sender recipient."""
    for persona in registry.personas():
        if persona.id != sender_id:
            return persona.display_name, persona.email_address
    raise ValueError("registry must have at least two personas to emit email")


def _parse_csv_rows(text: str) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
    """Parse CSV text into (headers, data_rows).  Falls back gracefully."""
    reader = csv.reader(io.StringIO(text.strip()))
    all_rows = [tuple(r) for r in reader if any(c.strip() for c in r)]
    if not all_rows:
        return ("description",), (("(empty)",),)
    headers = all_rows[0]
    data = tuple(all_rows[1:]) if len(all_rows) > 1 else (("",) * len(headers),)
    return headers, data


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
        profile = device.profile
        if not registry.is_permitted(persona.id, device.id, profile):
            raise ValueError(
                f"persona {persona.id} not permitted to emit {profile} on device {device.id}"
            )

        if profile == "email":
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

        elif profile == "xlsx_ledger":
            csv_content = gateway.complete(
                "artifact_content",
                (
                    f"Generate CSV ledger rows (header + data) for: {event.summary}. "
                    "Use columns: date,description,amount,category"
                ),
                cache_key=f"persona::{persona.id}",
            )
            headers, rows = _parse_csv_rows(csv_content)
            ledger_brief = XlsxLedgerBrief(
                creator_name=persona.display_name,
                created_at=event.timestamp,
                headers=headers,
                rows=rows,
                sheet_title=f"Ledger {event.id}",
                last_modified_by=persona.display_name,
            )
            written_xl = write_xlsx_ledger(ledger_brief, disclaimer=disclaimer)
            artifact = Artifact(
                id=f"art_{event.id}",
                owner_id=persona.id,
                device_id=device.id,
                profile="xlsx_ledger",
                filename=written_xl.filename,
                payload=written_xl.payload,
                sha256=written_xl.sha256,
                acquisition_time=event.timestamp,
                bound_proposition_ids=event.proposition_ids,
                signal_weight=1.0,
            )

        else:
            raise ValueError(f"unsupported profile {profile!r} on device {device.id}")

        ledger.record(artifact)
        artifacts.append(artifact)
    return artifacts
