"""Shared dataclasses for the pipeline.

These types are the contract between deep modules; keeping them in one place
avoids circular imports between Signal Ledger, Closure Verifier, Packager, etc.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


@dataclass(frozen=True)
class Proposition:
    """A discrete, checkable claim drawn from the canonical truth."""

    id: str
    text: str


@dataclass(frozen=True)
class PropositionGraph:
    propositions: tuple[Proposition, ...]


@dataclass(frozen=True)
class CanonicalTruth:
    outline: str
    graph: PropositionGraph


@dataclass(frozen=True)
class Persona:
    id: str
    display_name: str
    email_address: str


@dataclass(frozen=True)
class Device:
    """A device or account owned by a Persona or system actor, capable of emitting one profile."""

    id: str
    owner_id: str
    label: str
    profile: Literal["email", "pdf", "xlsx_ledger", "jpeg", "system_log_csv", "sms"]
    make: str = ""
    model: str = ""
    gps_capable: bool = False


@dataclass(frozen=True)
class Event:
    """A timestamped, persona-owned event bound to one or more propositions."""

    id: str
    timestamp: datetime
    actor_id: str
    device_id: str
    summary: str
    proposition_ids: tuple[str, ...]


@dataclass(frozen=True)
class Artifact:
    """A materialised file living under /corpus."""

    id: str
    owner_id: str
    device_id: str
    profile: Literal["email", "pdf", "xlsx_ledger", "jpeg", "system_log_csv", "sms"]
    filename: str
    payload: bytes
    sha256: str
    acquisition_time: datetime
    bound_proposition_ids: tuple[str, ...]
    signal_weight: float = 1.0


@dataclass(frozen=True)
class LedgerEntry:
    artifact_id: str
    owner_id: str
    device_id: str
    profile: str
    proposition_ids: tuple[str, ...]
    signal_weight: float


@dataclass
class ClosureResult:
    ok: bool
    gaps: list[ClosureGap] = field(default_factory=list)
    red_herring_gaps: list[RedHerringGap] = field(default_factory=list)
    dominance_gaps: list[DominanceGap] = field(default_factory=list)


@dataclass(frozen=True)
class NoiseSummary:
    """Aggregate counts for the haystack noise generation pass.

    Noise artifacts carry no bound propositions and are never recorded in the
    Signal Ledger; this summary is the only place noise is reported, as counts."""

    target_count: int
    generated_count: int
    rejected_count: int
    profile_distribution: dict[str, int]
    cache_hits: int
    cache_misses: int

    def to_dict(self) -> dict[str, object]:
        return {
            "target_count": self.target_count,
            "generated_count": self.generated_count,
            "rejected_count": self.rejected_count,
            "profile_distribution": self.profile_distribution,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
        }


@dataclass(frozen=True)
class ClosureGap:
    proposition_id: str
    required: int
    observed: int
    distinct_owners: tuple[str, ...]


@dataclass(frozen=True)
class DominanceGap:
    """A contested alternative that comes within the dominance margin of truth.

    ``alternative_id`` is the contested (false) proposition; ``alternative_support``
    is its aggregate signal; ``truth_support`` is the true account's aggregate
    signal; ``required_max_support`` is the ceiling an alternative must stay below
    (``truth_support * (1 - margin)``) and ``margin_shortfall`` is how far the
    alternative breaches that ceiling.
    """

    alternative_id: str
    truth_support: float
    alternative_support: float
    required_max_support: float
    margin_shortfall: float
    reason: str


@dataclass(frozen=True)
class RedHerring:
    """A false proposition refuted-by-construction.

    ``supporting`` are the plausible-but-weak artifacts that make the false
    lead tempting; ``breakers`` are the breaker bundle that conclusively
    refutes it *in aggregate*. No single breaker may convict on its own (each
    must pass the Smoking-Gun Critic), so a bundle is owner-distinct and
    typically fragmented across owners/devices/formats.
    """

    proposition: Proposition
    supporting: tuple[Artifact, ...]
    breakers: tuple[Artifact, ...]

    @property
    def support_signal(self) -> float:
        return sum(a.signal_weight for a in self.supporting)

    @property
    def breaker_signal(self) -> float:
        return sum(a.signal_weight for a in self.breakers)

    def distinct_breaker_owners(self) -> tuple[str, ...]:
        return tuple(sorted({a.owner_id for a in self.breakers}))


@dataclass(frozen=True)
class RedHerringGap:
    """A red herring whose breaker bundle fails the closure invariant."""

    proposition_id: str
    support_signal: float
    breaker_signal: float
    required_breaker_signal: float
    distinct_breaker_owners: tuple[str, ...]
    individually_convicting_breaker_ids: tuple[str, ...]
    reason: str
