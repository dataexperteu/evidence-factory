from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from ..llm.gateway import LLMGateway
from ..models import Artifact, ArtifactProfile, CaseBible, NoiseSummary
from ..provenance.catalog import ProvenanceCatalog
from ..registry import PersonaRegistry
from .guard import LeakContradictGuard

logger = logging.getLogger(__name__)

DEFAULT_TARGET = 300
MAX_COUNT = 1000
BATCH_SIZE = 10
# Safety valve: stop regenerating after this many total attempts per target slot.
MAX_ATTEMPTS_MULTIPLIER = 5

# Cycle through all six profiles to ensure even representation.
_PROFILE_CYCLE = [
    ArtifactProfile.EMAIL,
    ArtifactProfile.SMS,
    ArtifactProfile.PDF,
    ArtifactProfile.XLSX,
    ArtifactProfile.JPEG,
    ArtifactProfile.SYSTEM_LOG_CSV,
]


def _build_persona_context(registry: PersonaRegistry, bible: CaseBible) -> str:
    lines: list[str] = []
    for persona in bible.personas:
        devices = registry.devices_for(persona.name)
        device_desc = ", ".join(
            f"{d.device_id} ({'/'.join(p.value for p in sorted(d.permitted_profiles))})"
            for d in devices
        )
        lines.append(f"- {persona.name} ({persona.role}): devices=[{device_desc}]")
    return "\n".join(lines) if lines else "(no personas)"


class NoiseGenerator:
    """
    Generates persona/timeline-consistent haystack noise via Claude Haiku 4.5.

    Noise artifacts:
      - are NOT recorded in the Signal Ledger
      - carry no bound propositions
      - pass the leak/contradict guard before being accepted
      - are written through all six Provenance Catalog profiles
    """

    def __init__(
        self,
        llm: LLMGateway,
        catalog: ProvenanceCatalog,
        registry: PersonaRegistry,
        guard: LeakContradictGuard,
    ) -> None:
        self._llm = llm
        self._catalog = catalog
        self._registry = registry
        self._guard = guard

    def generate(
        self,
        bible: CaseBible,
        target_count: int = DEFAULT_TARGET,
        max_count: int = MAX_COUNT,
    ) -> tuple[list[Artifact], NoiseSummary]:
        """
        Generate up to target_count (capped at max_count) noise artifacts.
        Flagged artifacts are regenerated; the loop is bounded to prevent
        infinite retries.
        """
        effective_target = min(target_count, max_count)
        accepted: list[Artifact] = []
        total_rejected = 0
        profile_dist: dict[str, int] = {p.value: 0 for p in ArtifactProfile}
        max_attempts = effective_target * MAX_ATTEMPTS_MULTIPLIER

        persona_context = _build_persona_context(self._registry, bible)
        timeline_start = bible.master_timeline.start.isoformat()
        timeline_end = bible.master_timeline.end.isoformat()
        total_hours = bible.master_timeline.total_hours()

        attempt = 0
        profile_idx = 0

        while len(accepted) < effective_target and attempt < max_attempts:
            profile = _PROFILE_CYCLE[profile_idx % len(_PROFILE_CYCLE)]
            profile_idx += 1
            attempt += BATCH_SIZE

            raw_batch = self._llm.generate_noise_batch(
                persona_context=persona_context,
                timeline_start=timeline_start,
                timeline_end=timeline_end,
                total_hours=total_hours,
                taboo_topics=bible.taboo_topics,
                true_propositions=bible.true_propositions,
                profile=profile.value,
                case_description=bible.case_description,
                batch_size=BATCH_SIZE,
            )
            if not raw_batch:
                logger.warning("Empty batch returned for profile=%s; skipping", profile.value)
                continue

            artifact_batch = self._materialise_batch(raw_batch, profile, bible)
            if not artifact_batch:
                continue

            texts = [a.text_content for a in artifact_batch]
            flags = self._guard.check_batch(texts, bible.true_propositions)

            for artifact, rejected in zip(artifact_batch, flags, strict=False):
                if rejected:
                    total_rejected += 1
                    logger.debug("Guard rejected artifact (owner=%s)", artifact.owner)
                elif len(accepted) < effective_target:
                    accepted.append(artifact)
                    profile_dist[artifact.profile.value] = (
                        profile_dist.get(artifact.profile.value, 0) + 1
                    )

        if len(accepted) < effective_target:
            logger.warning(
                "Noise generator stopped after %d attempts; produced %d/%d artifacts",
                attempt,
                len(accepted),
                effective_target,
            )

        summary = NoiseSummary(
            target_count=effective_target,
            generated_count=len(accepted),
            rejected_count=total_rejected,
            profile_distribution={k: v for k, v in profile_dist.items() if v > 0},
            cache_hits=self._llm.cache_hits,
            cache_misses=self._llm.cache_misses,
        )
        return accepted, summary

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _materialise_batch(
        self,
        raw_batch: list[dict[str, Any]],
        fallback_profile: ArtifactProfile,
        bible: CaseBible,
    ) -> list[Artifact]:
        artifacts: list[Artifact] = []
        timeline = bible.master_timeline

        for item in raw_batch:
            owner = str(item.get("owner", ""))
            device = str(item.get("device", ""))
            text_content = str(item.get("text_content", ""))

            # Determine profile
            profile_str = str(item.get("profile", fallback_profile.value))
            try:
                profile = ArtifactProfile(profile_str)
            except ValueError:
                profile = fallback_profile

            # Validate triple against registry
            if not self._registry.is_permitted(owner, device, profile):
                logger.debug("Triple (%s, %s, %s) not permitted; skipping", owner, device, profile)
                continue

            # Compute timestamp
            offset_hours = float(item.get("timestamp_offset_hours", 0))
            offset_hours = max(0.0, min(offset_hours, timeline.total_hours()))
            timestamp = timeline.start + timedelta(hours=offset_hours)

            metadata: dict[str, Any] = item.get("metadata", {}) or {}
            if not isinstance(metadata, dict):
                metadata = {}

            # Inject timestamp into metadata for profile writers
            ts_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")
            if profile == ArtifactProfile.EMAIL:
                metadata.setdefault("date", timestamp.strftime("%a, %d %b %Y %H:%M:%S +0000"))
                metadata.setdefault("from", f"{owner}@evidence-factory.local")
            elif profile in (ArtifactProfile.SMS, ArtifactProfile.XLSX):
                metadata["timestamp"] = ts_str
            elif profile == ArtifactProfile.JPEG:
                metadata["datetime"] = timestamp.strftime("%Y:%m:%d %H:%M:%S")
                metadata.setdefault("device", device)
            elif profile == ArtifactProfile.PDF:
                metadata["created"] = "D:" + timestamp.strftime("%Y%m%d%H%M%S")
                metadata.setdefault("author", owner)
            elif profile == ArtifactProfile.SYSTEM_LOG_CSV:
                valid_persona_names = set(self._registry.persona_names())
                existing_entries: list[dict[str, Any]] = list(metadata.get("entries", []))
                if existing_entries:
                    # Validate: drop entries referencing unknown personas
                    validated = [
                        e for e in existing_entries
                        if str(e.get("persona_id", "")) in valid_persona_names
                    ]
                    if existing_entries and not validated:
                        logger.debug(
                            "All SYSTEM_LOG_CSV entries reference unknown persona IDs; skipping"
                        )
                        continue
                    metadata["entries"] = validated
                else:
                    # Build entries from text_content using registry personas
                    personas = sorted(valid_persona_names)
                    if not personas:
                        continue
                    built: list[dict[str, Any]] = []
                    for i, line in enumerate(text_content.splitlines()):
                        if not line.strip() or i >= 5:
                            break
                        entry_ts = timestamp + timedelta(minutes=i * 5)
                        if entry_ts > timeline.end:
                            entry_ts = timestamp
                        p_name = personas[i % len(personas)]
                        built.append(
                            {
                                "timestamp": entry_ts.isoformat(),
                                "badge_id": f"badge_{p_name}",
                                "persona_id": p_name,
                                "door_id": f"door_{i % 3}",
                                "granted": "true",
                            }
                        )
                    if not built:
                        p_name = personas[0]
                        built = [
                            {
                                "timestamp": timestamp.isoformat(),
                                "badge_id": f"badge_{p_name}",
                                "persona_id": p_name,
                                "door_id": "door_main",
                                "granted": "true",
                            }
                        ]
                    metadata["entries"] = built
                metadata.setdefault("schema", "access_log")
                metadata.setdefault("controller_id", device)

            try:
                content = self._catalog.write(profile, text_content, metadata)
            except Exception:
                logger.warning("Profile writer failed for %s; skipping", profile)
                continue

            artifacts.append(
                Artifact(
                    owner=owner,
                    device=device,
                    profile=profile,
                    timestamp=timestamp,
                    content=content,
                    text_content=text_content,
                    metadata=metadata,
                    is_noise=True,
                )
            )
        return artifacts
