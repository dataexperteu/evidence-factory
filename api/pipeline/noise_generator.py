"""Haystack Noise Generator.

Produces the persona/timeline-consistent mundane corpus that buries the
load-bearing artifacts. Noise artifacts:
  - are materialised through the per-profile writers under ``api/provenance/``
    (one writer per profile — never a monolithic catalog),
  - cover every profile present in the registry,
  - use only owner/device/profile triples the registry permits,
  - fall inside the case master timeline,
  - carry NO bound propositions and are NEVER recorded in the Signal Ledger,
  - pass the leak/contradict guard before being accepted.

Generation is batched (≥ 10 items per Haiku call) and the persona/timeline
context is sent under one prompt-cache key so cache hits are logged.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from ..provenance.artifact_writer import WrittenArtifact
from ..provenance.email_profile import EmailBrief, write_email
from ..provenance.jpeg_profile import JpegBrief, write_jpeg
from ..provenance.pdf_profile import PdfBrief, write_pdf
from ..provenance.sms_profile import SmsBrief, write_sms
from ..provenance.system_log_csv_profile import SystemLogBrief, write_system_log_csv
from ..provenance.xlsx_ledger_profile import XlsxLedgerBrief, write_xlsx_ledger
from .llm_gateway import LLMGateway
from .noise_guard import LeakContradictGuard
from .persona_registry import PersonaRegistry
from .types import Artifact, Device, NoiseSummary

LOG = logging.getLogger("evidence_factory.noise.generator")

DEFAULT_TARGET = 300
MAX_COUNT = 1000
BATCH_SIZE = 10
# Safety valve: stop after this many batch-attempts per target slot so a guard
# that rejects everything cannot spin forever.
_MAX_ATTEMPTS_MULTIPLIER = 5

_CONTEXT_CACHE_KEY = "noise::persona_timeline"


def _build_persona_context(registry: PersonaRegistry) -> str:
    lines: list[str] = []
    for persona in registry.personas():
        devices = registry.devices_for(persona.id)
        device_desc = ", ".join(f"{d.id} ({d.profile})" for d in devices)
        lines.append(f"- {persona.display_name}: devices=[{device_desc}]")
    return "\n".join(lines) if lines else "(no personas)"


class NoiseGenerator:
    def __init__(
        self,
        gateway: LLMGateway,
        registry: PersonaRegistry,
        guard: LeakContradictGuard,
    ) -> None:
        self._gateway = gateway
        self._registry = registry
        self._guard = guard
        self._seq = 0

    def generate(
        self,
        *,
        propositions: list[str],
        outline: str,
        timeline_start: datetime,
        timeline_end: datetime,
        disclaimer: str,
        target_count: int = DEFAULT_TARGET,
        max_count: int = MAX_COUNT,
    ) -> tuple[list[Artifact], NoiseSummary]:
        effective_target = max(0, min(target_count, max_count))
        triples = self._permitted_triples()
        if not triples or effective_target == 0:
            return [], NoiseSummary(
                target_count=effective_target,
                generated_count=0,
                rejected_count=0,
                profile_distribution={},
                cache_hits=self._gateway.stats.hits,
                cache_misses=self._gateway.stats.misses,
            )

        persona_context = _build_persona_context(self._registry)
        timeline_context = f"{timeline_start.isoformat()} .. {timeline_end.isoformat()}"

        accepted: list[Artifact] = []
        profile_dist: dict[str, int] = {}
        rejected = 0
        max_attempts = effective_target * _MAX_ATTEMPTS_MULTIPLIER
        attempt = 0
        idx = 0

        while len(accepted) < effective_target and attempt < max_attempts:
            owner_id, device = triples[idx % len(triples)]
            idx += 1
            attempt += BATCH_SIZE

            raw_batch = self._gateway.generate_noise_batch(
                profile=device.profile,
                batch_size=BATCH_SIZE,
                context_cache_key=_CONTEXT_CACHE_KEY,
                persona_context=persona_context,
                timeline_context=timeline_context,
                true_propositions=propositions,
                case_description=outline,
            )
            if not raw_batch:
                continue

            texts = [str(item.get("text_content", "")) for item in raw_batch]
            flags = self._guard.check_batch(texts, propositions)
            for item, text, reject in zip(raw_batch, texts, flags, strict=False):
                if reject:
                    rejected += 1
                    continue
                if len(accepted) >= effective_target:
                    break
                artifact = self._materialise(
                    owner_id, device, text, item, timeline_start, timeline_end, disclaimer
                )
                if artifact is None:
                    continue
                accepted.append(artifact)
                profile_dist[device.profile] = profile_dist.get(device.profile, 0) + 1

        if len(accepted) < effective_target:
            LOG.warning(
                "noise generator produced %d/%d artifacts after %d attempts",
                len(accepted),
                effective_target,
                attempt,
            )

        summary = NoiseSummary(
            target_count=effective_target,
            generated_count=len(accepted),
            rejected_count=rejected,
            profile_distribution=profile_dist,
            cache_hits=self._gateway.stats.hits,
            cache_misses=self._gateway.stats.misses,
        )
        return accepted, summary

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _permitted_triples(self) -> list[tuple[str, Device]]:
        """Every (owner_id, device) the registry permits — persona and system."""
        triples: list[tuple[str, Device]] = []
        for persona in self._registry.personas():
            for device in self._registry.devices_for(persona.id):
                triples.append((persona.id, device))
        for device in self._registry.system_devices():
            triples.append((device.owner_id, device))
        return triples

    def _timestamp(
        self, item: dict[str, object], start: datetime, end: datetime
    ) -> datetime:
        try:
            frac = float(item.get("timestamp_offset_hours", 0.0))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            frac = 0.0
        frac = max(0.0, min(frac, 1.0))
        span = (end - start).total_seconds()
        return start + timedelta(seconds=span * frac)

    def _materialise(
        self,
        owner_id: str,
        device: Device,
        text: str,
        item: dict[str, object],
        start: datetime,
        end: datetime,
        disclaimer: str,
    ) -> Artifact | None:
        if not self._registry.is_permitted(owner_id, device.id, device.profile):
            return None
        timestamp = self._timestamp(item, start, end)
        self._seq += 1
        seq = self._seq

        try:
            if device.profile == "email":
                written = self._write_email(owner_id, text, timestamp, seq, disclaimer)
            elif device.profile == "pdf":
                written = self._write_pdf(owner_id, text, timestamp, seq, disclaimer)
            elif device.profile == "xlsx_ledger":
                written = self._write_xlsx(owner_id, text, timestamp, seq, disclaimer)
            elif device.profile == "jpeg":
                written = self._write_jpeg(device, text, timestamp, disclaimer)
            elif device.profile == "sms":
                written = self._write_sms(owner_id, text, timestamp, disclaimer)
            elif device.profile == "system_log_csv":
                written = self._write_system_log(device, timestamp, seq, disclaimer)
            else:  # pragma: no cover - registry profiles are a closed set
                return None
        except Exception:
            LOG.warning("noise writer failed for profile=%s; skipping", device.profile)
            return None

        return Artifact(
            id=f"noise_{seq:06d}",
            owner_id=owner_id,
            device_id=device.id,
            profile=device.profile,
            filename=written.filename,
            payload=written.payload,
            sha256=written.sha256,
            acquisition_time=timestamp,
            bound_proposition_ids=(),
            signal_weight=0.0,
        )

    def _other_persona(self, owner_id: str) -> tuple[str, str]:  # (display_name, email)
        for persona in self._registry.personas():
            if persona.id != owner_id:
                return persona.display_name, persona.email_address
        persona = self._registry.get_persona(owner_id)
        return persona.display_name, persona.email_address

    def _write_email(
        self, owner_id: str, text: str, timestamp: datetime, seq: int, disclaimer: str
    ) -> WrittenArtifact:
        persona = self._registry.get_persona(owner_id)
        recipient = self._other_persona(owner_id)
        brief = EmailBrief(
            sender_name=persona.display_name,
            sender_address=persona.email_address,
            recipients=(recipient,),
            subject=f"Note {seq}",
            body=text,
            sent_at=timestamp,
        )
        return write_email(brief, disclaimer=disclaimer)

    def _write_pdf(
        self, owner_id: str, text: str, timestamp: datetime, seq: int, disclaimer: str
    ) -> WrittenArtifact:
        persona = self._registry.get_persona(owner_id)
        brief = PdfBrief(
            author_name=persona.display_name,
            title=f"Memo {seq}",
            body=text,
            created_at=timestamp,
        )
        return write_pdf(brief, disclaimer=disclaimer)

    def _write_xlsx(
        self, owner_id: str, text: str, timestamp: datetime, seq: int, disclaimer: str
    ) -> WrittenArtifact:
        persona = self._registry.get_persona(owner_id)
        brief = XlsxLedgerBrief(
            creator_name=persona.display_name,
            created_at=timestamp,
            headers=("date", "note"),
            rows=((timestamp.date().isoformat(), text),),
            sheet_title=f"Notes {seq}",
            last_modified_by=persona.display_name,
        )
        return write_xlsx_ledger(brief, disclaimer=disclaimer)

    def _write_jpeg(
        self, device: Device, text: str, timestamp: datetime, disclaimer: str
    ) -> WrittenArtifact:
        brief = JpegBrief(
            timestamp=timestamp,
            make=device.make,
            model=device.model,
            caption=text,
        )
        return write_jpeg(brief, disclaimer=disclaimer)

    def _write_sms(
        self, owner_id: str, text: str, timestamp: datetime, disclaimer: str
    ) -> WrittenArtifact:
        persona = self._registry.get_persona(owner_id)
        recipient_name, _ = self._other_persona(owner_id)
        brief = SmsBrief(
            sender_name=persona.display_name,
            sender_number=f"+1-555-{abs(hash(owner_id)) % 9000 + 1000:04d}",
            recipient_name=recipient_name,
            recipient_number=f"+1-555-{abs(hash(recipient_name)) % 9000 + 1000:04d}",
            body=text,
            sent_at=timestamp,
        )
        return write_sms(brief, disclaimer=disclaimer)

    def _write_system_log(
        self, device: Device, timestamp: datetime, seq: int, disclaimer: str
    ) -> WrittenArtifact:
        actor = self._registry.get_system_actor(device.owner_id)
        persona_ids = tuple(sorted(self._registry.persona_ids()))
        brief = SystemLogBrief(
            schema=actor.log_schema,
            device_id=device.id,
            event_id=f"noise{seq}",
            event_timestamp=timestamp,
            persona_ids=persona_ids,
        )
        return write_system_log_csv(brief, disclaimer=disclaimer)
