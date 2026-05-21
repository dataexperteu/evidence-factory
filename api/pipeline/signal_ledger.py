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
        self._critic_verdicts: list[dict] = []
        self._remediations: list[dict] = []

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

    def replace_entries(self, artifacts: list[Artifact]) -> None:
        """Rebuild artifact corroboration entries from the post-remediation set.

        Critic verdicts and remediation records are preserved — only the
        artifact ledger entries are recomputed."""
        self._entries = []
        for artifact in artifacts:
            self.record(artifact)

    def record_critic_verdict(self, verdict: dict) -> None:
        self._critic_verdicts.append(verdict)

    def record_remediation(self, record: dict) -> None:
        self._remediations.append(record)

    def critic_verdicts(self) -> list[dict]:
        return list(self._critic_verdicts)

    def remediations(self) -> list[dict]:
        return list(self._remediations)

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
