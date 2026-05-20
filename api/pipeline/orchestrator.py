"""Pipeline Orchestrator.

Wires the stages and produces a stream of progress events the FastAPI SSE
endpoint can publish. Implemented as an async generator so the orchestrator
itself does not depend on FastAPI.

Stages emitted: intake, extract, events, emit, critic, topup, close,
package, done. On failure (e.g. closure failure that top-up cannot fix) a
`failed` event with a structured payload is emitted instead of `done`.

Slice 8 adds the critic/remediator loop between `emit` and `close`:
1. Every load-bearing artifact is critiqued by the Smoking-Gun Critic.
2. Flagged artifacts (`too_strong: true`) are remediated via a randomly-
   chosen strategy (split | dilute); the resulting artifacts are re-critiqued.
3. Bounded retries (≤ MAX_REMEDIATION_ROUNDS) terminate the loop; a
   persistently-flagged artifact is logged but does not crash the job.
4. After remediation, the Closure Verifier re-runs; under-supported
   propositions get topped up with weak corroborators. If closure still
   fails after top-up, the job fails with a structured gap report.

The Signal Ledger records every critic verdict and remediation event so the
sealed `/SOLUTION` pack documents what was flagged and how it was defused.
"""

from __future__ import annotations

import asyncio
import logging
import random
import secrets
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

from ..provenance.email_profile import EmailBrief, write_email
from .artifact_emitter import emit_artifacts
from .attestation import SYNTHETIC_EVIDENCE_DISCLAIMER, gate
from .closure_verifier import verify_closure
from .event_graph import build_events
from .llm_gateway import LLMGateway
from .packager import PackagerInput, assert_separation, build_zip
from .persona_registry import PersonaRegistry, default_registry
from .remediator import choose_strategy, remediate
from .signal_ledger import SignalLedger
from .smoking_gun_critic import SmokingGunCritic
from .source_intake import ingest_paste
from .truth_extractor import extract_truth
from .types import (
    Artifact,
    CanonicalTruth,
    ClosureGap,
    RemediationRecord,
)

LOG = logging.getLogger("evidence_factory.orchestrator")

# Smoking-gun critic re-critic retry bound. PRD: "bounded re-critic retries
# terminate within 3 rounds; persistent `too_strong` after 3 rounds is logged
# but does not crash the job."
MAX_REMEDIATION_ROUNDS = 3


@dataclass(frozen=True)
class ProgressEvent:
    stage: str
    status: str  # "started" | "complete" | "failed"
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RunResult:
    run_id: str
    zip_bytes: bytes
    artifact_count: int
    proposition_count: int
    cache_hits: int


@dataclass
class RunSettings:
    min_owner_distinct: int = 2  # closure threshold; default keeps tests fast
    owners_per_proposition: int = 3  # > threshold so default runs pass closure
    max_propositions: int = 3


async def run_pipeline(
    source_paste: str,
    attestation_checked: bool,
    *,
    settings: RunSettings | None = None,
    gateway: LLMGateway | None = None,
) -> AsyncIterator[tuple[ProgressEvent, RunResult | None]]:
    """Yield (event, result_or_none). The final tuple has a RunResult on success."""
    settings = settings or RunSettings()
    gateway = gateway or LLMGateway()
    run_id = secrets.token_hex(8)
    # Fresh per-run RNG for strategy choice — separate from the gateway's RNG
    # so neither stream consumes the other's entropy.
    strategy_rng = random.Random()

    yield ProgressEvent("intake", "started", {"run_id": run_id}), None
    try:
        source = ingest_paste(source_paste)
        attestation = gate(attestation_checked)
    except Exception as e:
        yield ProgressEvent("intake", "failed", {"error": str(e)}), None
        return
    yield ProgressEvent("intake", "complete", {"chars": source.char_count}), None

    yield ProgressEvent("extract", "started"), None
    truth = extract_truth(source, gateway=gateway, max_propositions=settings.max_propositions)
    yield (
        ProgressEvent(
            "extract",
            "complete",
            {"propositions": len(truth.graph.propositions)},
        ),
        None,
    )

    yield ProgressEvent("events", "started"), None
    registry = default_registry()
    events = build_events(
        truth,
        registry,
        gateway=gateway,
        owners_per_proposition=settings.owners_per_proposition,
    )
    yield ProgressEvent("events", "complete", {"events": len(events)}), None

    yield ProgressEvent("emit", "started"), None
    ledger = SignalLedger()
    artifacts = emit_artifacts(
        events, registry, ledger, gateway=gateway, disclaimer=SYNTHETIC_EVIDENCE_DISCLAIMER
    )
    yield ProgressEvent("emit", "complete", {"artifacts": len(artifacts)}), None

    yield ProgressEvent("critic", "started"), None
    critic = SmokingGunCritic(gateway=gateway)
    artifacts, flagged, persistent = _run_critic_loop(
        artifacts,
        truth,
        registry,
        ledger,
        critic=critic,
        gateway=gateway,
        rng=strategy_rng,
    )
    yield (
        ProgressEvent(
            "critic",
            "complete",
            {
                "flagged": flagged,
                "persistent_after_retries": persistent,
                "max_rounds": MAX_REMEDIATION_ROUNDS,
            },
        ),
        None,
    )

    yield ProgressEvent("topup", "started"), None
    closure = verify_closure(truth.graph, ledger, settings.min_owner_distinct)
    topup_added = 0
    if not closure.ok:
        topup_artifacts = _topup_corroborators(
            closure.gaps,
            truth,
            registry,
            ledger,
            gateway=gateway,
            min_owner_distinct=settings.min_owner_distinct,
        )
        artifacts.extend(topup_artifacts)
        topup_added = len(topup_artifacts)
        closure = verify_closure(truth.graph, ledger, settings.min_owner_distinct)
    yield ProgressEvent("topup", "complete", {"added": topup_added}), None

    yield ProgressEvent("close", "started"), None
    if not closure.ok:
        gap_payload = [
            {
                "proposition_id": g.proposition_id,
                "required": g.required,
                "observed": g.observed,
                "distinct_owners": list(g.distinct_owners),
            }
            for g in closure.gaps
        ]
        yield ProgressEvent("close", "failed", {"gaps": gap_payload}), None
        return
    yield ProgressEvent("close", "complete", {"propositions": len(truth.graph.propositions)}), None

    yield ProgressEvent("package", "started"), None
    zip_bytes = build_zip(
        PackagerInput(
            truth=truth,
            artifacts=artifacts,
            ledger=ledger,
            registry=registry,
            attestation_text=attestation.as_text(),
            disclaimer=SYNTHETIC_EVIDENCE_DISCLAIMER,
            run_id=run_id,
            remediation_log=ledger.remediation_log_json(),
        )
    )
    assert_separation(zip_bytes)
    yield ProgressEvent("package", "complete", {"bytes": len(zip_bytes)}), None

    result = RunResult(
        run_id=run_id,
        zip_bytes=zip_bytes,
        artifact_count=len(artifacts),
        proposition_count=len(truth.graph.propositions),
        cache_hits=gateway.stats.hits,
    )
    yield (
        ProgressEvent(
            "done",
            "complete",
            {
                "run_id": run_id,
                "artifacts": result.artifact_count,
                "cache_hits": result.cache_hits,
            },
        ),
        result,
    )


def _run_critic_loop(
    artifacts: list[Artifact],
    truth: CanonicalTruth,
    registry: PersonaRegistry,
    ledger: SignalLedger,
    *,
    critic: SmokingGunCritic,
    gateway: LLMGateway,
    rng: random.Random,
) -> tuple[list[Artifact], int, int]:
    """Run the smoking-gun critic + remediator loop.

    Returns (updated_artifacts, total_flagged, persistent_after_retries).
    Records every verdict and remediation onto the Signal Ledger so the
    sealed pack documents the audit trail.
    """
    final = list(artifacts)
    flagged = 0
    persistent = 0
    # Snapshot the original list so iteration is not perturbed by
    # remediation appending new artifacts to `final`.
    for original in artifacts:
        verdict = critic.critique(original, truth, round=0)
        ledger.record_verdict(verdict)
        if not verdict.too_strong:
            continue
        flagged += 1
        strategy = choose_strategy(rng)
        current = original
        initial_reason = verdict.reason
        final_reason = verdict.reason
        new_ids: list[str] = []
        rounds = 0
        final_too_strong = True
        while rounds < MAX_REMEDIATION_ROUNDS:
            rounds += 1
            output = remediate(
                current,
                registry,
                strategy=strategy,
                gateway=gateway,
                disclaimer=SYNTHETIC_EVIDENCE_DISCLAIMER,
            )
            ledger.remove(current.id)
            final = [a for a in final if a.id != current.id]
            for new_art in output.new_artifacts:
                ledger.record(new_art)
                final.append(new_art)
            new_ids.extend(a.id for a in output.new_artifacts)
            # Re-critique the new artifacts. If any are still too_strong,
            # focus the next remediation round on the first such offender.
            offender: Artifact | None = None
            for new_art in output.new_artifacts:
                re_verdict = critic.critique(new_art, truth, round=rounds)
                ledger.record_verdict(re_verdict)
                final_reason = re_verdict.reason
                if re_verdict.too_strong and offender is None:
                    offender = new_art
            if offender is None:
                final_too_strong = False
                break
            current = offender
        if final_too_strong:
            persistent += 1
            LOG.warning(
                "remediation_persistent original=%s strategy=%s rounds=%d",
                original.id,
                strategy,
                rounds,
            )
        ledger.record_remediation(
            RemediationRecord(
                original_artifact_id=original.id,
                strategy=strategy,
                new_artifact_ids=tuple(new_ids),
                rounds=rounds,
                final_too_strong=final_too_strong,
                initial_reason=initial_reason,
                final_reason=final_reason,
            )
        )
    return final, flagged, persistent


def _topup_corroborators(
    gaps: list[ClosureGap],
    truth: CanonicalTruth,
    registry: PersonaRegistry,
    ledger: SignalLedger,
    *,
    gateway: LLMGateway,
    min_owner_distinct: int,
) -> list[Artifact]:
    """Emit weak corroborators for every under-supported proposition.

    Picks email-capable owners not yet bound to the proposition. If the
    registry has fewer such owners than the deficit, the gap remains and
    the subsequent closure check will surface it as a structured failure.
    """
    new_artifacts: list[Artifact] = []
    prop_text_by_id = {p.id: p.text for p in truth.graph.propositions}
    for gap in gaps:
        deficit = max(0, gap.required - gap.observed)
        if deficit == 0:
            continue
        used = ledger.distinct_owners_for(gap.proposition_id)
        candidates = [
            p
            for p in registry.personas()
            if p.id not in used and any(d.profile == "email" for d in registry.devices_for(p.id))
        ]
        if not candidates:
            continue  # closure re-run will report the residual gap
        for index, persona in enumerate(candidates[:deficit]):
            device = next(d for d in registry.devices_for(persona.id) if d.profile == "email")
            other = next(p for p in registry.personas() if p.id != persona.id)
            prop_text = prop_text_by_id.get(gap.proposition_id, gap.proposition_id)
            body = gateway.complete(
                "artifact_content",
                f"Draft a weak corroborator email touching on {prop_text}",
                cache_key=f"topup::{gap.proposition_id}::{persona.id}",
            )
            # Top-up timestamps walk forward from a fixed epoch so they are
            # tz-aware and don't collide with original-emission timestamps.
            acquisition_time = datetime(2024, 6, 10, 12, 0, tzinfo=UTC) + timedelta(hours=index)
            brief = EmailBrief(
                sender_name=persona.display_name,
                sender_address=persona.email_address,
                recipients=((other.display_name, other.email_address),),
                subject=f"Re: {gap.proposition_id} (top-up corroborator)",
                body=body,
                sent_at=acquisition_time,
            )
            written = write_email(brief, disclaimer=SYNTHETIC_EVIDENCE_DISCLAIMER)
            artifact = Artifact(
                id=f"art_topup_{gap.proposition_id}_{persona.id}",
                owner_id=persona.id,
                device_id=device.id,
                profile="email",
                filename=written.filename,
                payload=written.payload,
                sha256=written.sha256,
                acquisition_time=acquisition_time,
                bound_proposition_ids=(gap.proposition_id,),
                signal_weight=0.5,
            )
            ledger.record(artifact)
            new_artifacts.append(artifact)
            # Stop early if this proposition is already topped up.
            if len(ledger.distinct_owners_for(gap.proposition_id)) >= min_owner_distinct:
                break
    return new_artifacts


async def collect_run(
    source_paste: str,
    attestation_checked: bool,
    *,
    settings: RunSettings | None = None,
    gateway: LLMGateway | None = None,
) -> tuple[list[ProgressEvent], RunResult | None]:
    """Convenience for tests — drain the generator into a list."""
    events: list[ProgressEvent] = []
    result: RunResult | None = None
    async for event, maybe_result in run_pipeline(
        source_paste, attestation_checked, settings=settings, gateway=gateway
    ):
        events.append(event)
        if maybe_result is not None:
            result = maybe_result
    return events, result


def run_sync(
    source_paste: str,
    attestation_checked: bool,
    *,
    settings: RunSettings | None = None,
    gateway: LLMGateway | None = None,
) -> tuple[list[ProgressEvent], RunResult | None]:
    return asyncio.run(
        collect_run(source_paste, attestation_checked, settings=settings, gateway=gateway)
    )
