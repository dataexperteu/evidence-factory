"""Closure Verifier — corroboration lower bound + red-herring + dominance.

PRD invariants enforced here:

- *lower bound* — every truth proposition must have at least N owner-distinct
  corroborating artifacts;
- *red-herring invariant* — every red herring's breaker bundle must refute it
  *in aggregate* by a configurable margin, no breaker artifact may convict on
  its own, and the bundle must be owner-distinct with at least two artifacts;
- *dominance* — the true proposition graph must remain the uniquely
  best-supported account: every contested alternative's aggregate support must
  stay a configurable margin below the truth's aggregate support.

This module is the correctness backbone — characterization tests pin the
gap-report shape so subsequent slices cannot drift the contract silently.
"""

from __future__ import annotations

from .llm_gateway import SMOKING_GUN_WEIGHT_THRESHOLD
from .signal_ledger import SignalLedger
from .types import (
    ClosureGap,
    ClosureResult,
    DominanceGap,
    PropositionGraph,
    RedHerring,
    RedHerringGap,
)

DEFAULT_BREAKER_MARGIN = 0.5
MIN_BREAKER_OWNERS = 2
DEFAULT_DOMINANCE_MARGIN = 0.5


def verify_closure(
    graph: PropositionGraph,
    ledger: SignalLedger,
    min_owner_distinct: int,
    *,
    red_herrings: list[RedHerring] | None = None,
    breaker_margin: float = DEFAULT_BREAKER_MARGIN,
    dominance_margin: float = DEFAULT_DOMINANCE_MARGIN,
    smoking_gun_threshold: float = SMOKING_GUN_WEIGHT_THRESHOLD,
) -> ClosureResult:
    """Return pass/fail + structured gap report.

    A corroboration gap is emitted for every proposition whose owner-distinct
    corroborator count is below `min_owner_distinct`. A red-herring gap is
    emitted for every red herring whose breaker bundle fails the invariant. A
    dominance gap is emitted for every contested alternative (red herring or
    otherwise) whose aggregate support comes within `dominance_margin` of the
    true account's aggregate support. All lists are sorted by proposition id so
    reports are stable across runs.
    """
    if min_owner_distinct < 1:
        raise ValueError("min_owner_distinct must be >= 1")
    if breaker_margin < 0:
        raise ValueError("breaker_margin must be >= 0")
    if not 0 <= dominance_margin < 1:
        raise ValueError("dominance_margin must be in [0, 1)")
    gaps: list[ClosureGap] = []
    for prop in graph.propositions:
        owners = ledger.distinct_owners_for(prop.id)
        if len(owners) < min_owner_distinct:
            gaps.append(
                ClosureGap(
                    proposition_id=prop.id,
                    required=min_owner_distinct,
                    observed=len(owners),
                    distinct_owners=tuple(sorted(owners)),
                )
            )
    gaps.sort(key=lambda g: g.proposition_id)

    rh_gaps: list[RedHerringGap] = []
    for rh in red_herrings or []:
        gap = _check_red_herring(rh, breaker_margin, smoking_gun_threshold)
        if gap is not None:
            rh_gaps.append(gap)
    rh_gaps.sort(key=lambda g: g.proposition_id)

    truth_support = _truth_support(graph, ledger)
    dom_gaps: list[DominanceGap] = []
    for rh in red_herrings or []:
        dom_gap = _check_dominance(rh, truth_support, dominance_margin)
        if dom_gap is not None:
            dom_gaps.append(dom_gap)
    dom_gaps.sort(key=lambda g: g.alternative_id)

    return ClosureResult(
        ok=not gaps and not rh_gaps and not dom_gaps,
        gaps=gaps,
        red_herring_gaps=rh_gaps,
        dominance_gaps=dom_gaps,
    )


def _truth_support(graph: PropositionGraph, ledger: SignalLedger) -> float:
    """Aggregate signal supporting the true account.

    Sum of signal weights of every ledger entry that corroborates at least one
    true proposition. An artifact bound to several true propositions counts
    once (entries are per-artifact), so this is the account's total signal mass.
    """
    truth_ids = {p.id for p in graph.propositions}
    return sum(
        e.signal_weight for e in ledger.entries() if truth_ids.intersection(e.proposition_ids)
    )


def _check_dominance(
    rh: RedHerring, truth_support: float, dominance_margin: float
) -> DominanceGap | None:
    alt_support = rh.support_signal
    ceiling = truth_support * (1.0 - dominance_margin)
    if alt_support <= ceiling:
        return None
    return DominanceGap(
        alternative_id=rh.proposition.id,
        truth_support=truth_support,
        alternative_support=alt_support,
        required_max_support=ceiling,
        margin_shortfall=alt_support - ceiling,
        reason=(
            f"contested alternative support {alt_support:.3f} comes within "
            f"dominance margin {dominance_margin} of truth support "
            f"{truth_support:.3f} (must stay <= {ceiling:.3f})"
        ),
    )


def _check_red_herring(
    rh: RedHerring, breaker_margin: float, smoking_gun_threshold: float
) -> RedHerringGap | None:
    support_signal = rh.support_signal
    breaker_signal = rh.breaker_signal
    required = support_signal * (1.0 + breaker_margin)
    owners = rh.distinct_breaker_owners()
    convicting = tuple(b.id for b in rh.breakers if b.signal_weight >= smoking_gun_threshold)

    reasons: list[str] = []
    if breaker_signal < required:
        reasons.append(
            f"breaker aggregate {breaker_signal:.3f} does not exceed support "
            f"{support_signal:.3f} by margin {breaker_margin} (need >= {required:.3f})"
        )
    if convicting:
        reasons.append(f"breaker artifacts individually convict: {list(convicting)}")
    if len(owners) < MIN_BREAKER_OWNERS:
        reasons.append(
            f"breaker bundle is not owner-distinct enough: {len(owners)} owner(s), "
            f"need >= {MIN_BREAKER_OWNERS}"
        )
    if not reasons:
        return None
    return RedHerringGap(
        proposition_id=rh.proposition.id,
        support_signal=support_signal,
        breaker_signal=breaker_signal,
        required_breaker_signal=required,
        distinct_breaker_owners=owners,
        individually_convicting_breaker_ids=convicting,
        reason="; ".join(reasons),
    )


class ClosureFailure(Exception):
    """Raised when the pipeline cannot ship under-corroborated propositions."""

    def __init__(self, result: ClosureResult):
        self.result = result
        summary = ", ".join(f"{g.proposition_id}({g.observed}/{g.required})" for g in result.gaps)
        super().__init__(f"closure failed: {summary}")
