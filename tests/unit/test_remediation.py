"""Remediation pass — bounded re-critic loop + closure top-up.

Asserts the loop terminates within MAX_REMEDIATION_ROUNDS, that a persistently
`too_strong` artifact is logged but does not crash the job, and that the closure
top-up emits weak corroborators (or fails with a gap report when it cannot).
"""

from datetime import UTC, datetime

from api.pipeline.critic import SmokingGunCritic
from api.pipeline.llm_gateway import LLMGateway
from api.pipeline.persona_registry import default_registry
from api.pipeline.remediation import (
    MAX_REMEDIATION_ROUNDS,
    run_critique_pass,
    top_up_closure,
)
from api.pipeline.remediator import FixedStrategySelector
from api.pipeline.signal_ledger import SignalLedger
from api.pipeline.types import (
    Artifact,
    CanonicalTruth,
    Proposition,
    PropositionGraph,
)
from api.provenance.email_profile import EmailBrief, write_email

DISCLAIMER = "SYNTHETIC"
BASE = datetime(2024, 6, 3, 9, 0, tzinfo=UTC)


class _AlwaysTooStrong(LLMGateway):
    """Gateway whose critic verdict is always `too_strong` — drives the retry cap."""

    def complete(self, role, prompt, *, cache_key=None):
        return '{"too_strong": true, "reason": "persistently damning"}'


def _artifact(art_id, owner_id, props, weight=1.0):
    registry = default_registry()
    persona = registry.get_persona(owner_id)
    device = registry.devices_for(owner_id)[0]
    other = next(p for p in registry.personas() if p.id != owner_id)
    brief = EmailBrief(
        sender_name=persona.display_name,
        sender_address=persona.email_address,
        recipients=((other.display_name, other.email_address),),
        subject=f"note {art_id}",
        body="This is the decisive piece of evidence in the matter.",
        sent_at=BASE,
    )
    written = write_email(brief, disclaimer=DISCLAIMER)
    return Artifact(
        id=art_id,
        owner_id=owner_id,
        device_id=device.id,
        profile="email",
        filename=written.filename,
        payload=written.payload,
        sha256=written.sha256,
        acquisition_time=BASE,
        bound_proposition_ids=props,
        signal_weight=weight,
    )


def _truth():
    return CanonicalTruth(
        outline="case",
        graph=PropositionGraph(propositions=(Proposition(id="prop_1", text="X"),)),
    )


def test_full_strength_artifact_is_remediated_and_loop_terminates():
    registry = default_registry()
    ledger = SignalLedger()
    critic = SmokingGunCritic(LLMGateway(mode="fixture"))
    art = _artifact("art_1", "p_holmes", ("prop_1",), weight=1.0)

    final, log = run_critique_pass(
        [art],
        _truth(),
        registry,
        critic=critic,
        selector=FixedStrategySelector("dilute"),
        ledger=ledger,
        disclaimer=DISCLAIMER,
    )

    assert log and log[0].outcome == "remediated"
    assert log[0].strategy == "dilute"
    # All surviving artifacts cleared the critic (weight below the bar).
    assert all(a.signal_weight < 1.0 for a in final)
    # Verdicts and the remediation are recorded on the ledger.
    assert ledger.critic_verdicts()
    assert ledger.remediations()
    # Ledger entries reflect the post-remediation set.
    assert {e.artifact_id for e in ledger.entries()} == {a.id for a in final}


def test_persistent_too_strong_is_logged_after_retry_cap_without_crashing():
    registry = default_registry()
    ledger = SignalLedger()
    critic = SmokingGunCritic(_AlwaysTooStrong(mode="fixture"))
    art = _artifact("art_1", "p_holmes", ("prop_1",), weight=1.0)

    final, log = run_critique_pass(
        [art],
        _truth(),
        registry,
        critic=critic,
        selector=FixedStrategySelector("dilute"),
        ledger=ledger,
        disclaimer=DISCLAIMER,
    )

    failures = [r for r in log if r.outcome == "failed_after_retries"]
    assert failures, "a persistently too-strong artifact must be logged as a failure"
    assert all(r.round <= MAX_REMEDIATION_ROUNDS for r in log)
    # The job did not crash: it returns a finalized artifact set.
    assert final


def test_top_up_emits_weak_corroborators_for_under_threshold_proposition():
    registry = default_registry()
    ledger = SignalLedger()
    # prop_1 has a single owner-distinct corroborator; threshold is 2.
    ledger.record(_artifact("a1", "p_holmes", ("prop_1",), weight=0.5))
    graph = PropositionGraph(propositions=(Proposition(id="prop_1", text="X"),))

    added, closure = top_up_closure(
        graph, ledger, registry, min_owner_distinct=2, disclaimer=DISCLAIMER, base_time=BASE
    )

    assert len(added) == 1
    assert closure.ok
    assert ledger.distinct_owners_for("prop_1") >= {"p_holmes"}
    assert len(ledger.distinct_owners_for("prop_1")) == 2
    # Top-up corroborators are weak — below the smoking-gun bar.
    assert all(a.signal_weight < 1.0 for a in added)


def test_top_up_fails_with_gap_report_when_owners_are_exhausted():
    registry = default_registry()  # four personas
    ledger = SignalLedger()
    ledger.record(_artifact("a1", "p_holmes", ("prop_1",)))
    graph = PropositionGraph(propositions=(Proposition(id="prop_1", text="X"),))

    # Demand more owner-distinct corroborators than the registry can supply.
    added, closure = top_up_closure(
        graph, ledger, registry, min_owner_distinct=9, disclaimer=DISCLAIMER, base_time=BASE
    )

    assert not closure.ok
    assert closure.gaps[0].proposition_id == "prop_1"
    # It topped up as far as it could (all spare owners) before reporting the gap.
    assert len(added) == len(registry.personas()) - 1
