"""Pipeline Orchestrator.

Wires the stages and produces a stream of progress events the FastAPI SSE
endpoint can publish. Implemented as an async generator so the orchestrator
itself does not depend on FastAPI.

Stages emitted: intake, extract, events, emit, close, package, done.
On failure (e.g. closure failure) a `failed` event with a structured payload
is emitted instead of `done`.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field

from .artifact_emitter import emit_artifacts
from .attestation import SYNTHETIC_EVIDENCE_DISCLAIMER, gate
from .closure_verifier import verify_closure
from .event_graph import build_events
from .llm_gateway import LLMGateway
from .packager import PackagerInput, assert_separation, build_zip
from .persona_registry import default_registry_with_pdf
from .signal_ledger import SignalLedger
from .source_intake import ingest_paste
from .truth_extractor import extract_truth

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
    registry = default_registry_with_pdf()
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

    yield ProgressEvent("close", "started"), None
    closure = verify_closure(truth.graph, ledger, settings.min_owner_distinct)
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
