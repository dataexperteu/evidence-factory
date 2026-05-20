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
    profile: Literal["email", "jpeg_exif_photo"]
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
    location: tuple[float, float] | None = None  # (lat, lon) decimal degrees; used by GPS-capable profiles


@dataclass(frozen=True)
class Artifact:
    """A materialised file living under /corpus."""

    id: str
    owner_id: str
    device_id: str
    profile: Literal["email", "jpeg_exif_photo"]
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
