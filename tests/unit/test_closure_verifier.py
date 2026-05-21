"""Closure Verifier — corroboration-only invariant (slice 1).

These are characterization tests: they pin the gap-report shape so later
slices that extend closure (dominance, smoking-gun, red-herrings) cannot
drift the slice-1 contract silently.
"""

from datetime import UTC, datetime

import pytest

from api.pipeline.closure_verifier import ClosureFailure, verify_closure
from api.pipeline.signal_ledger import SignalLedger
from api.pipeline.types import (
    Artifact,
    Proposition,
    PropositionGraph,
    RedHerring,
)


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


# -- red-herring invariant (slice 9) ---------------------------------------


def _weighted_art(art_id: str, owner: str, prop: str, weight: float) -> Artifact:
    return Artifact(
        id=art_id,
        owner_id=owner,
        device_id=f"d_{owner}",
        profile="email",
        filename=f"{art_id}.eml",
        payload=b"",
        sha256="0" * 64,
        acquisition_time=datetime(2024, 1, 1, tzinfo=UTC),
        bound_proposition_ids=(prop,),
        signal_weight=weight,
    )


def _red_herring(*, support: float, breakers: list[tuple[str, float]]) -> RedHerring:
    prop = Proposition(id="prop_rh", text="false lead")
    supporting = (_weighted_art("rh_sup", "owner_s", "prop_rh", support),)
    breaker_arts = tuple(
        _weighted_art(f"rh_brk_{owner}", owner, "prop_rh", w) for owner, w in breakers
    )
    return RedHerring(proposition=prop, supporting=supporting, breakers=breaker_arts)


def _full_ledger(graph: PropositionGraph) -> SignalLedger:
    """A ledger where every true proposition is fully corroborated."""
    led = SignalLedger()
    for prop in graph.propositions:
        for owner in ("o1", "o2"):
            led.record(_art(f"a_{owner}_{prop.id}", owner, (prop.id,)))
    return led


def test_red_herring_passes_when_breakers_refute_by_margin():
    graph = _graph("p1")
    led = _full_ledger(graph)
    rh = _red_herring(support=0.4, breakers=[("o_a", 0.45), ("o_b", 0.45)])
    result = verify_closure(graph, led, min_owner_distinct=2, red_herrings=[rh], breaker_margin=0.5)
    assert result.ok is True
    assert result.red_herring_gaps == []


def test_red_herring_rejected_when_breaker_aggregate_below_margin():
    graph = _graph("p1")
    led = _full_ledger(graph)
    # support 0.6 -> required 0.9; breakers only sum to 0.7.
    rh = _red_herring(support=0.6, breakers=[("o_a", 0.35), ("o_b", 0.35)])
    result = verify_closure(graph, led, min_owner_distinct=2, red_herrings=[rh], breaker_margin=0.5)
    assert result.ok is False
    assert len(result.red_herring_gaps) == 1
    gap = result.red_herring_gaps[0]
    assert gap.proposition_id == "prop_rh"
    assert gap.breaker_signal < gap.required_breaker_signal


def test_red_herring_rejected_when_a_breaker_individually_convicts():
    graph = _graph("p1")
    led = _full_ledger(graph)
    # Aggregate is fine, but one breaker is at/above the smoking-gun bar.
    rh = _red_herring(support=0.4, breakers=[("o_a", 1.0), ("o_b", 0.45)])
    result = verify_closure(graph, led, min_owner_distinct=2, red_herrings=[rh], breaker_margin=0.5)
    assert result.ok is False
    assert result.red_herring_gaps[0].individually_convicting_breaker_ids == ("rh_brk_o_a",)


def test_red_herring_rejected_when_bundle_not_owner_distinct():
    graph = _graph("p1")
    led = _full_ledger(graph)
    # Two breakers but both owned by the same custodian.
    rh = _red_herring(support=0.4, breakers=[("o_a", 0.45), ("o_a", 0.45)])
    result = verify_closure(graph, led, min_owner_distinct=2, red_herrings=[rh], breaker_margin=0.5)
    assert result.ok is False
    assert len(result.red_herring_gaps[0].distinct_breaker_owners) < 2


def test_red_herring_margin_is_configurable():
    graph = _graph("p1")
    led = _full_ledger(graph)
    rh = _red_herring(support=0.5, breakers=[("o_a", 0.45), ("o_b", 0.45)])  # aggregate 0.9
    # margin 0.5 -> required 0.75 -> passes
    assert verify_closure(
        graph, led, min_owner_distinct=2, red_herrings=[rh], breaker_margin=0.5
    ).ok
    # margin 1.0 -> required 1.0 -> fails (0.9 < 1.0)
    assert not verify_closure(
        graph, led, min_owner_distinct=2, red_herrings=[rh], breaker_margin=1.0
    ).ok
