"""Closure Verifier — corroboration-only for slice 1.

PRD invariant (lower bound): every truth proposition must have at least N
owner-distinct corroborating artifacts. Slice 1 implements only this
corroboration check; smoking-gun upper-bound, dominance, and red-herring
refutation land in later slices.

This module is the correctness backbone — characterization tests pin the
gap-report shape so subsequent slices cannot drift the contract silently.
"""

from __future__ import annotations

from .signal_ledger import SignalLedger
from .types import ClosureGap, ClosureResult, PropositionGraph


def verify_closure(
    graph: PropositionGraph,
    ledger: SignalLedger,
    min_owner_distinct: int,
) -> ClosureResult:
    """Return pass/fail + structured gap report.

    A gap is emitted for every proposition whose owner-distinct corroborator
    count is below `min_owner_distinct`. The gaps list is sorted by
    proposition id so reports are stable across runs.
    """
    if min_owner_distinct < 1:
        raise ValueError("min_owner_distinct must be >= 1")
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
    return ClosureResult(ok=not gaps, gaps=gaps)


class ClosureFailure(Exception):
    """Raised when the pipeline cannot ship under-corroborated propositions."""

    def __init__(self, result: ClosureResult):
        self.result = result
        summary = ", ".join(f"{g.proposition_id}({g.observed}/{g.required})" for g in result.gaps)
        super().__init__(f"closure failed: {summary}")
