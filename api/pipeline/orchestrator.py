"""Pipeline Orchestrator.

Wires the stages and produces a stream of progress events the FastAPI SSE
endpoint can publish. Implemented as an async generator so the orchestrator
itself does not depend on FastAPI.

Stages emitted: intake, extract, events, emit, critique, close, package, done.
On failure (e.g. closure failure) a `failed` event with a structured payload
is emitted instead of `done`.
"""

from __future__ import annotations

import asyncio
import logging
import random
import secrets
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

from .artifact_emitter import emit_artifacts
from .attestation import SYNTHETIC_EVIDENCE_DISCLAIMER, gate
from .closure_verifier import verify_closure
from .critic import SmokingGunCritic
from .event_graph import build_events
from .llm_gateway import LLMGateway
from .noise_generator import NoiseGenerator
from .noise_guard import LeakContradictGuard
from .packager import PackagerInput, assert_separation, build_zip
from .persona_registry import default_registry
from .remediation import run_critique_pass, top_up_closure
from .remediator import RandomStrategySelector
from .signal_ledger import SignalLedger
from .source_intake import ingest_paste
from .truth_extractor import extract_truth

_TOPUP_BASE_TIME = datetime(2024, 6, 10, 9, 0, 0, tzinfo=UTC)

LOG = logging.getLogger("evidence_factory.orchestrator")


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
    remediation_count: int = 0
    noise_count: int = 0


@dataclass
class RunSettings:
    min_owner_distinct: int = 2  # closure threshold; default keeps tests fast
    owners_per_proposition: int = 3  # > threshold so default runs pass closure
    max_propositions: int = 3
    noise_target: int = 300  # PRD default haystack volume
    noise_max: int = 1000  # PRD hard cap


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

    yield ProgressEvent("critique", "started"), None
    critic = SmokingGunCritic(gateway)
    selector = RandomStrategySelector(random.Random(secrets.randbits(64)))
    artifacts, remediation_log = run_critique_pass(
        artifacts,
        truth,
        registry,
        critic=critic,
        selector=selector,
        ledger=ledger,
        disclaimer=SYNTHETIC_EVIDENCE_DISCLAIMER,
    )
    remediated = sum(1 for r in remediation_log if r.outcome == "remediated")
    unresolved = sum(1 for r in remediation_log if r.outcome == "failed_after_retries")
    yield (
        ProgressEvent(
            "critique",
            "complete",
            {"flagged": len(remediation_log), "remediated": remediated, "unresolved": unresolved},
        ),
        None,
    )

    yield ProgressEvent("close", "started"), None
    closure = verify_closure(truth.graph, ledger, settings.min_owner_distinct)
    if not closure.ok:
        # Remediation may have dropped a proposition below threshold; top it up
        # with weak corroborators and re-check before failing.
        added, closure = top_up_closure(
            truth.graph,
            ledger,
            registry,
            settings.min_owner_distinct,
            disclaimer=SYNTHETIC_EVIDENCE_DISCLAIMER,
            base_time=_TOPUP_BASE_TIME,
        )
        artifacts.extend(added)
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

    yield ProgressEvent("noise", "started"), None
    noise_generator = NoiseGenerator(gateway, registry, LeakContradictGuard(gateway))
    timeline_start = min((e.timestamp for e in events), default=_TOPUP_BASE_TIME)
    timeline_end = max((e.timestamp for e in events), default=_TOPUP_BASE_TIME)
    if timeline_end <= timeline_start:
        timeline_end = timeline_start + timedelta(days=1)
    noise_artifacts, noise_summary = noise_generator.generate(
        propositions=[p.text for p in truth.graph.propositions],
        outline=truth.outline,
        timeline_start=timeline_start,
        timeline_end=timeline_end,
        disclaimer=SYNTHETIC_EVIDENCE_DISCLAIMER,
        target_count=settings.noise_target,
        max_count=settings.noise_max,
    )
    yield (
        ProgressEvent(
            "noise",
            "complete",
            {"generated": noise_summary.generated_count, "rejected": noise_summary.rejected_count},
        ),
        None,
    )

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
            remediation_log=[r.to_json_serialisable() for r in remediation_log],
            noise_artifacts=noise_artifacts,
            noise_summary=noise_summary.to_dict(),
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
        remediation_count=remediated,
        noise_count=noise_summary.generated_count,
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
