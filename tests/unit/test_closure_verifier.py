"""Closure Verifier — corroboration-only invariant (slice 1).

These are characterization tests: they pin the gap-report shape so later
slices that extend closure (dominance, smoking-gun, red-herrings) cannot
drift the slice-1 contract silently.
"""

from datetime import UTC, datetime

import pytest

from api.pipeline.closure_verifier import ClosureFailure, verify_closure
from api.pipeline.signal_ledger import SignalLedger
from api.pipeline.types import Artifact, Proposition, PropositionGraph


def _graph(*ids: str) -> PropositionGraph:
    return PropositionGraph(propositions=tuple(Proposition(id=i, text=i) for i in ids))


def _art(art_id: str, owner: str, props: tuple[str, ...]) -> Artifact:
    return Artifact(
        id=art_id,
        owner_id=owner,
        device_id=f"d_{owner}",
        profile="email",
        filename=f"{art_id}.eml",
        payload=b"",
        sha256="0" * 64,
        acquisition_time=datetime(2024, 1, 1, tzinfo=UTC),
        bound_proposition_ids=props,
    )


def test_passes_when_every_proposition_has_min_owner_distinct():
    graph = _graph("p1", "p2")
    led = SignalLedger()
    for owner in ("o1", "o2"):
        led.record(_art(f"a_{owner}_p1", owner, ("p1",)))
        led.record(_art(f"a_{owner}_p2", owner, ("p2",)))

    result = verify_closure(graph, led, min_owner_distinct=2)
    assert result.ok is True
    assert result.gaps == []


def test_emits_gap_for_under_corroborated_proposition():
    graph = _graph("p1", "p2")
    led = SignalLedger()
    # p1 has only one owner; p2 has two.
    led.record(_art("a1", "o1", ("p1",)))
    led.record(_art("a2", "o1", ("p2",)))
    led.record(_art("a3", "o2", ("p2",)))

    result = verify_closure(graph, led, min_owner_distinct=2)
    assert result.ok is False
    assert len(result.gaps) == 1
    gap = result.gaps[0]
    assert gap.proposition_id == "p1"
    assert gap.required == 2
    assert gap.observed == 1
    assert gap.distinct_owners == ("o1",)


def test_owner_distinct_ignores_multiple_artifacts_from_same_owner():
    # Two artifacts from the same owner count as one owner-distinct corroborator —
    # this is the slice-1 invariant the verifier must enforce.
    graph = _graph("p1")
    led = SignalLedger()
    led.record(_art("a1", "o1", ("p1",)))
    led.record(_art("a2", "o1", ("p1",)))
    led.record(_art("a3", "o1", ("p1",)))

    result = verify_closure(graph, led, min_owner_distinct=2)
    assert result.ok is False
    assert result.gaps[0].observed == 1


def test_gaps_sorted_by_proposition_id_for_stable_reports():
    graph = _graph("p_zeta", "p_alpha", "p_mid")
    led = SignalLedger()  # totally empty -> every proposition is a gap
    result = verify_closure(graph, led, min_owner_distinct=1)
    assert [g.proposition_id for g in result.gaps] == ["p_alpha", "p_mid", "p_zeta"]


def test_min_owner_distinct_must_be_positive():
    with pytest.raises(ValueError):
        verify_closure(_graph("p1"), SignalLedger(), min_owner_distinct=0)


def test_closure_failure_message_summarises_gaps():
    graph = _graph("p1")
    led = SignalLedger()
    result = verify_closure(graph, led, min_owner_distinct=2)
    err = ClosureFailure(result)
    assert "p1(0/2)" in str(err)
