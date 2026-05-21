"""Closure Verifier — corroboration lower bound + red-herring invariant.

PRD invariants enforced here:

- *lower bound* — every truth proposition must have at least N owner-distinct
  corroborating artifacts;
- *red-herring invariant* — every red herring's breaker bundle must refute it
  *in aggregate* by a configurable margin, no breaker artifact may convict on
  its own, and the bundle must be owner-distinct with at least two artifacts.

This module is the correctness backbone — characterization tests pin the
gap-report shape so subsequent slices cannot drift the contract silently.
"""

from __future__ import annotations

from .llm_gateway import SMOKING_GUN_WEIGHT_THRESHOLD
from .signal_ledger import SignalLedger
from .types import ClosureGap, ClosureResult, PropositionGraph, RedHerring, RedHerringGap

DEFAULT_BREAKER_MARGIN = 0.5
MIN_BREAKER_OWNERS = 2


def verify_closure(
    graph: PropositionGraph,
    ledger: SignalLedger,
    min_owner_distinct: int,
    *,
    red_herrings: list[RedHerring] | None = None,
    breaker_margin: float = DEFAULT_BREAKER_MARGIN,
    smoking_gun_threshold: float = SMOKING_GUN_WEIGHT_THRESHOLD,
) -> ClosureResult:
    """Return pass/fail + structured gap report.

    A corroboration gap is emitted for every proposition whose owner-distinct
    corroborator count is below `min_owner_distinct`. A red-herring gap is
    emitted for every red herring whose breaker bundle fails the invariant.
    Both lists are sorted by proposition id so reports are stable across runs.
    """
    if min_owner_distinct < 1:
        raise ValueError("min_owner_distinct must be >= 1")
    if breaker_margin < 0:
        raise ValueError("breaker_margin must be >= 0")
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

    return ClosureResult(ok=not gaps and not rh_gaps, gaps=gaps, red_herring_gaps=rh_gaps)


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
