"""Remediation pass — ties the Smoking-Gun Critic to the Remediator.

Two pieces live here:

- ``run_critique_pass`` — critiques every load-bearing artifact, remediates the
  ones flagged `too_strong`, and re-critiques the products with a bounded retry
  (<= 3 rounds). Every verdict and every remediation is recorded on the Signal
  Ledger; a per-flag remediation log is returned for the sealed pack.
- ``top_up_closure`` — after all events, emits extra weak corroborators for any
  proposition whose owner-distinct corroboration dropped below threshold due to
  remediation, then re-checks closure.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..provenance.email_profile import EmailBrief, write_email
from .closure_verifier import verify_closure
from .critic import SmokingGunCritic
from .persona_registry import PersonaRegistry
from .remediator import DILUTE_FACTOR, StrategySelector, remediate
from .signal_ledger import SignalLedger
from .types import Artifact, CanonicalTruth, ClosureResult, PropositionGraph

MAX_REMEDIATION_ROUNDS = 3

# Weight for top-up corroborators: well below the smoking-gun bar so they never
# themselves get flagged, while still counting as owner-distinct corroboration.
WEAK_CORROBORATOR_WEIGHT = DILUTE_FACTOR / 2


@dataclass(frozen=True)
class RemediationRecord:
    flagged_artifact_id: str
    reason: str
    strategy: str
    round: int
    produced_artifact_ids: tuple[str, ...]
    outcome: str  # "remediated" | "failed_after_retries"

    def to_json_serialisable(self) -> dict:
        return {
            "flagged_artifact_id": self.flagged_artifact_id,
            "reason": self.reason,
            "strategy": self.strategy,
            "round": self.round,
            "produced_artifact_ids": list(self.produced_artifact_ids),
            "outcome": self.outcome,
        }


def run_critique_pass(
    artifacts: list[Artifact],
    truth: CanonicalTruth,
    registry: PersonaRegistry,
    *,
    critic: SmokingGunCritic,
    selector: StrategySelector,
    ledger: SignalLedger,
    disclaimer: str,
    max_rounds: int = MAX_REMEDIATION_ROUNDS,
) -> tuple[list[Artifact], list[RemediationRecord]]:
    """Critique + remediate every artifact. Returns (final_artifacts, log).

    A worklist drives bounded retries: remediation products are re-critiqued at
    `round + 1`. An artifact still flagged after `max_rounds` is logged as a
    failure and kept as-is — the job continues rather than crashing.
    """
    worklist: deque[tuple[Artifact, int]] = deque((a, 0) for a in artifacts)
    final: list[Artifact] = []
    log: list[RemediationRecord] = []

    while worklist:
        artifact, round_no = worklist.popleft()
        verdict = critic.critique(artifact, truth)
        ledger.record_critic_verdict(verdict.to_json_serialisable())

        if not verdict.too_strong:
            final.append(artifact)
            continue

        if round_no >= max_rounds:
            log.append(
                RemediationRecord(
                    flagged_artifact_id=artifact.id,
                    reason=verdict.reason,
                    strategy="none",
                    round=round_no,
                    produced_artifact_ids=(),
                    outcome="failed_after_retries",
                )
            )
            final.append(artifact)
            continue

        strategy = selector.choose(artifact)
        products = remediate(artifact, strategy, registry, disclaimer=disclaimer)
        record = RemediationRecord(
            flagged_artifact_id=artifact.id,
            reason=verdict.reason,
            strategy=strategy,
            round=round_no,
            produced_artifact_ids=tuple(p.id for p in products),
            outcome="remediated",
        )
        log.append(record)
        ledger.record_remediation(record.to_json_serialisable())
        for product in products:
            worklist.append((product, round_no + 1))

    ledger.replace_entries(final)
    return final, log


def top_up_closure(
    graph: PropositionGraph,
    ledger: SignalLedger,
    registry: PersonaRegistry,
    min_owner_distinct: int,
    *,
    disclaimer: str,
    base_time: datetime,
) -> tuple[list[Artifact], ClosureResult]:
    """Emit weak corroborators for under-threshold propositions, then re-check.

    Returns (added_artifacts, closure_result). The added artifacts are recorded
    on the ledger as a side effect so the re-check reflects them.
    """
    added: list[Artifact] = []
    for prop_index, prop in enumerate(graph.propositions):
        owners = ledger.distinct_owners_for(prop.id)
        deficit = min_owner_distinct - len(owners)
        if deficit <= 0:
            continue
        spare = [p for p in registry.personas() if p.id not in owners]
        for owner_index, persona in enumerate(spare[:deficit]):
            device = registry.devices_for(persona.id)[0]
            recipient = _other_persona(registry, persona.id)
            sent_at = base_time + timedelta(days=prop_index, hours=owner_index)
            brief = EmailBrief(
                sender_name=persona.display_name,
                sender_address=persona.email_address,
                recipients=(recipient,),
                subject=f"Re: following up ({prop.id})",
                body=(
                    "Just adding my two cents — this lines up with what I noticed too, "
                    "for whatever little that's worth."
                ),
                sent_at=sent_at,
            )
            written = write_email(brief, disclaimer=disclaimer)
            artifact = Artifact(
                id=f"art_topup_{prop.id}_{persona.id}",
                owner_id=persona.id,
                device_id=device.id,
                profile="email",
                filename=written.filename,
                payload=written.payload,
                sha256=written.sha256,
                acquisition_time=sent_at,
                bound_proposition_ids=(prop.id,),
                signal_weight=WEAK_CORROBORATOR_WEIGHT,
            )
            ledger.record(artifact)
            added.append(artifact)
    return added, verify_closure(graph, ledger, min_owner_distinct)


def _other_persona(registry: PersonaRegistry, owner_id: str) -> tuple[str, str]:
    for persona in registry.personas():
        if persona.id != owner_id:
            return persona.display_name, persona.email_address
    raise ValueError("registry must have at least two personas to emit email")
