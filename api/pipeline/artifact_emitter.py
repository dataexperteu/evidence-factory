"""Artifact Emitter — turns Events into written artifacts.

Routes each event to the correct Provenance Catalog profile based on its
device's profile field:
  - "email"          → email_profile.write_email
  - "system_log_csv" → system_log_csv_profile.write_access_log / write_cdr
    (variant chosen from the owning System's log_variant attribute)

System log events carry subject_ids listing the persona ids that appear as
rows in the emitted CSV. The emitter validates every referenced persona_id
against the registry before writing; unknown ids are rejected.
"""

from __future__ import annotations

from datetime import timedelta

from ..provenance.email_profile import EmailBrief, write_email
from ..provenance.system_log_csv_profile import (
    AccessLogBrief,
    AccessLogRow,
    CdrBrief,
    CdrRow,
    write_access_log,
    write_cdr,
)
from .llm_gateway import LLMGateway
from .persona_registry import PersonaRegistry
from .signal_ledger import SignalLedger
from .types import Artifact, Event


def _pick_recipient(registry: PersonaRegistry, sender_id: str) -> tuple[str, str]:
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


def _validate_subject_personas(registry: PersonaRegistry, subject_ids: tuple[str, ...]) -> None:
    for pid in subject_ids:
        if not registry.is_known_persona(pid):
            raise ValueError(
                f"system log references persona_id {pid!r} which is not in the registry"
            )


def _emit_system_log(
    event: Event,
    registry: PersonaRegistry,
    *,
    disclaimer: str,
) -> Artifact:
    device = registry.get_device(event.device_id)
    system = registry.get_system(event.actor_id)
    subject_ids = event.subject_ids

    if not subject_ids:
        raise ValueError(
            f"system log event {event.id} has no subject_ids — cannot build CSV rows"
        )
    _validate_subject_personas(registry, subject_ids)

    window_start = event.timestamp
    window_end = event.timestamp + timedelta(hours=8)

    if system.log_variant == "access_log":
        rows = tuple(
            AccessLogRow(
                timestamp=window_start + timedelta(minutes=i * 15),
                controller_id=system.id,
                badge_id=f"badge_{pid[-4:]}",
                persona_id=pid,
                door_id="main-entrance",
                granted=True,
            )
            for i, pid in enumerate(subject_ids)
        )
        brief = AccessLogBrief(
            system_id=system.id,
            rows=rows,
            window_start=window_start,
            window_end=window_end,
        )
        written = write_access_log(brief, disclaimer=disclaimer)
    else:
        if len(subject_ids) < 2:
            raise ValueError(
                f"CDR event {event.id} needs at least 2 subject_ids (caller + callee)"
            )
        calling_id, called_id = subject_ids[0], subject_ids[1]
        row = CdrRow(
            call_id=f"call_{event.id}",
            start_time=window_start,
            end_time=window_start + timedelta(minutes=7),
            calling_persona_id=calling_id,
            called_persona_id=called_id,
            calling_number=f"+44-20-7946-{calling_id[-4:]}",
            called_number=f"+44-20-7946-{called_id[-4:]}",
            direction="outbound",
        )
        brief_cdr = CdrBrief(
            system_id=system.id,
            rows=(row,),
            window_start=window_start,
            window_end=window_end,
        )
        written = write_cdr(brief_cdr, disclaimer=disclaimer)

    return Artifact(
        id=f"art_{event.id}",
        owner_id=system.id,
        device_id=device.id,
        profile="system_log_csv",
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
        elif device.profile == "system_log_csv":
            artifact = _emit_system_log(event, registry, disclaimer=disclaimer)
        else:
            raise ValueError(f"unhandled device profile: {device.profile!r}")
        ledger.record(artifact)
        artifacts.append(artifact)
    return artifacts
