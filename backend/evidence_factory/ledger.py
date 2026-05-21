from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SignalEntry:
    artifact_id: str
    propositions: list[str]
    signal_weight: float = 1.0


class SignalLedger:
    def __init__(self) -> None:
        self._entries: dict[str, SignalEntry] = {}

    def add_signal(
        self,
        artifact_id: str,
        propositions: list[str],
        signal_weight: float = 1.0,
    ) -> None:
        self._entries[artifact_id] = SignalEntry(
            artifact_id=artifact_id,
            propositions=propositions,
            signal_weight=signal_weight,
        )

    def has_signal(self, artifact_id: str) -> bool:
        return artifact_id in self._entries

    def all_signal_artifact_ids(self) -> set[str]:
        return set(self._entries.keys())

    def corroboration_count(self, proposition: str) -> int:
        return sum(1 for e in self._entries.values() if proposition in e.propositions)

    def to_dict(self) -> dict[str, Any]:
        return {
            artifact_id: {
                "propositions": entry.propositions,
                "signal_weight": entry.signal_weight,
            }
            for artifact_id, entry in self._entries.items()
        }

    def __len__(self) -> int:
        return len(self._entries)


@dataclass
class NoiseExclusion:
    """Tracks that noise is excluded from the signal ledger."""

    noise_artifact_ids: set[str] = field(default_factory=set)

    def record(self, artifact_id: str) -> None:
        self.noise_artifact_ids.add(artifact_id)

    def is_noise(self, artifact_id: str) -> bool:
        return artifact_id in self.noise_artifact_ids
