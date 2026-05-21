"""Signal Ledger.

Records artifact → proposition bindings with signal weight. The Closure
Verifier consumes the ledger; queries here must be cheap and exact.

Slice 1 invariants:
- `signal_weight` is always 1.0 (skeleton — later slices vary weight).
- "Owner-distinct corroborators per proposition" is the single closure
  predicate the Verifier uses.
"""

from __future__ import annotations

from .types import Artifact, LedgerEntry


class SignalLedger:
    def __init__(self) -> None:
        self._entries: list[LedgerEntry] = []

    def record(self, artifact: Artifact) -> None:
        self._entries.append(
            LedgerEntry(
                artifact_id=artifact.id,
                owner_id=artifact.owner_id,
                device_id=artifact.device_id,
                profile=artifact.profile,
                proposition_ids=artifact.bound_proposition_ids,
                signal_weight=artifact.signal_weight,
            )
        )

    def entries(self) -> list[LedgerEntry]:
        return list(self._entries)

    def corroborators_for(self, proposition_id: str) -> list[LedgerEntry]:
        return [e for e in self._entries if proposition_id in e.proposition_ids]

    def distinct_owners_for(self, proposition_id: str) -> set[str]:
        return {e.owner_id for e in self.corroborators_for(proposition_id)}

    def to_json_serialisable(self) -> list[dict]:
        return [
            {
                "artifact_id": e.artifact_id,
                "owner_id": e.owner_id,
                "device_id": e.device_id,
                "profile": e.profile,
                "proposition_ids": list(e.proposition_ids),
                "signal_weight": e.signal_weight,
            }
            for e in self._entries
        ]
