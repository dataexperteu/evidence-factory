"""Smoking-Gun Critic.

`critique(artifact, truth) -> CriticVerdict{too_strong, reason}`. After every
load-bearing artifact is emitted, the critic judges whether that single artifact
— on its own — would solve the case or trivially prove a bound proposition. A
`too_strong` verdict triggers the Remediator.

The verdict is structured JSON; `parse_verdict` is the contract surface that
later slices and contract tests pin. Exact prose is never asserted — only the
schema and the boolean invariant.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .llm_gateway import LLMGateway
from .types import Artifact, CanonicalTruth


class VerdictSchemaError(ValueError):
    """Raised when an LLM critic response does not match the verdict schema."""


@dataclass(frozen=True)
class CriticVerdict:
    artifact_id: str
    too_strong: bool
    reason: str

    def to_json_serialisable(self) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "too_strong": self.too_strong,
            "reason": self.reason,
        }


def parse_verdict(raw: str, artifact_id: str) -> CriticVerdict:
    """Parse a critic LLM response into a CriticVerdict.

    Schema: a JSON object with a boolean `too_strong` and an optional string
    `reason`. Anything else raises VerdictSchemaError so a malformed model
    response fails loudly rather than silently passing an artifact through.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise VerdictSchemaError(f"critic response is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise VerdictSchemaError(f"critic verdict must be a JSON object, got {type(data).__name__}")
    if "too_strong" not in data or not isinstance(data["too_strong"], bool):
        raise VerdictSchemaError("critic verdict must carry a boolean `too_strong`")
    reason = data.get("reason", "")
    if not isinstance(reason, str):
        raise VerdictSchemaError("critic verdict `reason` must be a string when present")
    return CriticVerdict(artifact_id=artifact_id, too_strong=data["too_strong"], reason=reason)


class SmokingGunCritic:
    """Judges load-bearing artifacts through the LLM Gateway's `critic` role."""

    def __init__(self, gateway: LLMGateway) -> None:
        self._gateway = gateway

    def critique(self, artifact: Artifact, truth: CanonicalTruth) -> CriticVerdict:
        raw = self._gateway.complete("critic", self._prompt(artifact, truth))
        return parse_verdict(raw, artifact.id)

    def _prompt(self, artifact: Artifact, truth: CanonicalTruth) -> str:
        excerpt = artifact.payload.decode("utf-8", errors="replace")[:600]
        bound = ", ".join(artifact.bound_proposition_ids)
        return (
            "You are a smoking-gun critic. Decide whether this single artifact, on "
            "its own, would solve the case or trivially prove a key proposition.\n"
            f"artifact_id={artifact.id}\n"
            f"signal_weight={artifact.signal_weight}\n"
            f"bound_propositions={bound}\n"
            f"case_outline={truth.outline[:300]}\n"
            f"content_excerpt={excerpt}\n"
            'Respond with JSON: {"too_strong": bool, "reason": str}.'
        )
