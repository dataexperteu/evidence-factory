"""Signal Ledger.

Records artifact → proposition bindings with signal weight. The Closure
Verifier consumes the ledger; queries here must be cheap and exact.

Slice 1 invariants:
- `signal_weight` is always 1.0 (skeleton — later slices vary weight).
- "Owner-distinct corroborators per proposition" is the single closure
  predicate the Verifier uses.
"""

from __future__ import annotations

from .types import Artifact, CriticVerdict, LedgerEntry, RemediationRecord


class SignalLedger:
    def __init__(self) -> None:
        self._entries: list[LedgerEntry] = []
        self._verdicts: list[CriticVerdict] = []
        self._remediations: list[RemediationRecord] = []

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

    def remove(self, artifact_id: str) -> None:
        """Drop a ledger entry. Used by the Remediator when an artifact is
        replaced (split into fragments / diluted in place)."""
        self._entries = [e for e in self._entries if e.artifact_id != artifact_id]

    def entries(self) -> list[LedgerEntry]:
        return list(self._entries)

    def corroborators_for(self, proposition_id: str) -> list[LedgerEntry]:
        return [e for e in self._entries if proposition_id in e.proposition_ids]

    def distinct_owners_for(self, proposition_id: str) -> set[str]:
        return {e.owner_id for e in self.corroborators_for(proposition_id)}

    # -- critic / remediation audit log ----------------------------------

    def record_verdict(self, verdict: CriticVerdict) -> None:
        self._verdicts.append(verdict)

    def record_remediation(self, record: RemediationRecord) -> None:
        self._remediations.append(record)

    def verdicts(self) -> list[CriticVerdict]:
        return list(self._verdicts)

    def remediations(self) -> list[RemediationRecord]:
        return list(self._remediations)

    def remediation_log_json(self) -> dict:
        return {
            "verdicts": [
                {
                    "artifact_id": v.artifact_id,
                    "too_strong": v.too_strong,
                    "reason": v.reason,
                    "round": v.round,
                }
                for v in self._verdicts
            ],
            "remediations": [
                {
                    "original_artifact_id": r.original_artifact_id,
                    "strategy": r.strategy,
                    "new_artifact_ids": list(r.new_artifact_ids),
                    "rounds": r.rounds,
                    "final_too_strong": r.final_too_strong,
                    "initial_reason": r.initial_reason,
                    "final_reason": r.final_reason,
                }
                for r in self._remediations
            ],
        }

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
