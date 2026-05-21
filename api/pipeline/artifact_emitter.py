"""Artifact Emitter — turns Events into written artifacts.

Dispatch is device-profile-driven.  System-actor events are detected first
(registry.is_system_actor), then persona events are dispatched on device.profile.

Profile → writer mapping:
  email          → api/provenance/email_profile.py
  pdf            → api/provenance/pdf_profile.py
  xlsx_ledger    → api/provenance/xlsx_ledger_profile.py
  jpeg           → api/provenance/jpeg_profile.py
  system_log_csv → api/provenance/system_log_csv_profile.py
  sms            → api/provenance/sms_profile.py
"""

from __future__ import annotations

import csv
import io

from ..provenance.email_profile import EmailBrief, write_email
from ..provenance.jpeg_profile import JpegBrief, write_jpeg
from ..provenance.pdf_profile import PdfBrief, write_pdf
from ..provenance.sms_profile import SmsBrief, write_sms
from ..provenance.system_log_csv_profile import SystemLogBrief, write_system_log_csv
from ..provenance.xlsx_ledger_profile import XlsxLedgerBrief, write_xlsx_ledger
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


def _parse_csv_rows(text: str) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
    """Parse CSV text into (headers, data_rows).  Falls back gracefully."""
    reader = csv.reader(io.StringIO(text.strip()))
    all_rows = [tuple(r) for r in reader if any(c.strip() for c in r)]
    if not all_rows:
        return ("description",), (("(empty)",),)
    headers = all_rows[0]
    data = tuple(all_rows[1:]) if len(all_rows) > 1 else (("",) * len(headers),)
    return headers, data


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


def _emit_pdf(
    event: Event,
    persona: Persona,
    device: Device,
    registry: PersonaRegistry,
    gateway: LLMGateway,
    disclaimer: str,
) -> Artifact:
    if not registry.is_permitted(persona.id, device.id, "pdf"):
        raise ValueError(
            f"persona {persona.id} not permitted to emit pdf on device {device.id}"
        )
    body = gateway.complete(
        "artifact_content",
        f"Draft content matching: {event.summary}",
        cache_key=f"persona::{persona.id}",
    )
    brief = PdfBrief(
        author_name=persona.display_name,
        title=f"Re: {event.proposition_ids[0]} note ({event.id})",
        body=body,
        created_at=event.timestamp,
    )
    written = write_pdf(brief, disclaimer=disclaimer)
    return Artifact(
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


def _emit_xlsx_ledger(
    event: Event,
    persona: Persona,
    device: Device,
    registry: PersonaRegistry,
    gateway: LLMGateway,
    disclaimer: str,
) -> Artifact:
    if not registry.is_permitted(persona.id, device.id, "xlsx_ledger"):
        raise ValueError(
            f"persona {persona.id} not permitted to emit xlsx_ledger on device {device.id}"
        )
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
    written = write_xlsx_ledger(ledger_brief, disclaimer=disclaimer)
    return Artifact(
        id=f"art_{event.id}",
        owner_id=persona.id,
        device_id=device.id,
        profile="xlsx_ledger",
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


def _emit_sms(
    event: Event,
    persona: Persona,
    device: Device,
    registry: PersonaRegistry,
    gateway: LLMGateway,
    disclaimer: str,
) -> Artifact:
    if not registry.is_permitted(persona.id, device.id, "sms"):
        raise ValueError(
            f"persona {persona.id} not permitted to emit sms on device {device.id}"
        )
    # Find a recipient persona for the SMS exchange
    recipient_persona = None
    for p in registry.personas():
        if p.id != persona.id:
            recipient_persona = p
            break
    if recipient_persona is None:
        raise ValueError("registry must have at least two personas to emit sms")
    body = gateway.complete(
        "artifact_content",
        f"Write a short SMS chat exchange matching: {event.summary}",
        cache_key=f"persona::{persona.id}",
    )
    brief = SmsBrief(
        sender_name=persona.display_name,
        sender_number=f"+1-555-{abs(hash(persona.id)) % 9000 + 1000:04d}",
        recipient_name=recipient_persona.display_name,
        recipient_number=f"+1-555-{abs(hash(recipient_persona.id)) % 9000 + 1000:04d}",
        body=body,
        sent_at=event.timestamp,
    )
    written = write_sms(brief, disclaimer=disclaimer)
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


def _emit_system_log(
    event: Event,
    registry: PersonaRegistry,
    ledger: SignalLedger,
    disclaimer: str,
) -> Artifact:
    system_actor = registry.get_system_actor(event.actor_id)
    device = registry.get_device(event.device_id)
    if not registry.is_permitted(system_actor.id, device.id, "system_log_csv"):
        raise ValueError(
            f"system actor {system_actor.id} not permitted to emit system_log_csv "
            f"on device {device.id}"
        )
    # Row subjects: all personas in the registry; validate and reject unknown IDs.
    persona_ids = tuple(sorted(registry.persona_ids()))
    if not persona_ids:
        raise ValueError("registry must contain at least one persona for system log rows")
    registry.validate_persona_ids(persona_ids)
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
        # System-actor events are handled first (log branch design)
        if registry.is_system_actor(event.actor_id):
            artifact = _emit_system_log(event, registry, ledger, disclaimer=disclaimer)
            artifacts.append(artifact)
            continue

        persona = registry.get_persona(event.actor_id)
        device = registry.get_device(event.device_id)

        if device.profile == "email":
            artifact = _emit_email(event, persona, device, registry, gateway, disclaimer)
        elif device.profile == "pdf":
            artifact = _emit_pdf(event, persona, device, registry, gateway, disclaimer)
        elif device.profile == "xlsx_ledger":
            artifact = _emit_xlsx_ledger(event, persona, device, registry, gateway, disclaimer)
        elif device.profile == "jpeg":
            artifact = _emit_jpeg(event, persona, device, registry, gateway, disclaimer)
        elif device.profile == "sms":
            artifact = _emit_sms(event, persona, device, registry, gateway, disclaimer)
        else:
            raise ValueError(
                f"unsupported profile {device.profile!r} on device {device.id}"
            )

        ledger.record(artifact)
        artifacts.append(artifact)
    return artifacts
