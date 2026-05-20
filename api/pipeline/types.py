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
    """A device or account owned by a Persona, capable of emitting one profile."""

    id: str
    owner_id: str
    label: str
    profile: Literal["email"]


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
    profile: Literal["email"]
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


@dataclass(frozen=True)
class ClosureGap:
    proposition_id: str
    required: int
    observed: int
    distinct_owners: tuple[str, ...]


@dataclass(frozen=True)
class CriticVerdict:
    """One Smoking-Gun Critic verdict for an artifact.

    `round` = 0 for the initial pass; 1+ for re-critic after remediation. The
    same artifact_id may have multiple verdicts across rounds.
    """

    artifact_id: str
    too_strong: bool
    reason: str
    round: int = 0


@dataclass(frozen=True)
class RemediationRecord:
    """One critic-flag → strategy → outcome triple for the audit log.

    Recorded on the Signal Ledger so the sealed `/SOLUTION` pack documents
    what was flagged and how it was defused.
    """

    original_artifact_id: str
    strategy: str  # "split" | "dilute" (slice 8) — "redact-relocate" | "demote" land later
    new_artifact_ids: tuple[str, ...]
    rounds: int
    final_too_strong: bool  # True ⇒ persistent after MAX rounds, logged-and-continued
    initial_reason: str
    final_reason: str
