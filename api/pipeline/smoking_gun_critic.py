"""Smoking-Gun Critic.

PRD invariant (upper bound): no single load-bearing artifact may, on its own,
prove a key truth proposition. After every load-bearing artifact emission, a
critic LLM (Sonnet 4.6 / Opus 4.7) inspects the artifact and emits a
structured verdict ``{too_strong: bool, reason: str}``. Verdicts are logged
on the Signal Ledger and trigger the Remediator when `too_strong=True`.

Schema contract (locked):
    {
      "too_strong": <bool>,        # REQUIRED
      "reason":     <str>          # REQUIRED, free-form, no length cap
    }

`parse_verdict` validates the schema and is the single entry point for
turning a raw LLM response into a `CriticVerdict`. Contract tests assert
schema validity against recorded LLM responses by feeding raw JSON through
this function.

This slice ships a *fixture-mode* critic so the orchestrator wire-up and the
end-to-end smoke test reliably exercise the remediation loop without a live
LLM call. Real mode passes the prompt through the LLM Gateway and parses
its JSON response.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

from .llm_gateway import LLMGateway
from .types import Artifact, CanonicalTruth, CriticVerdict


class CriticSchemaError(ValueError):
    """Raised when a critic verdict response does not validate against the
    locked verdict schema. Surfaces the underlying parse/shape error so
    contract-test failures are diagnosable."""


def parse_verdict(artifact_id: str, raw: str, round: int = 0) -> CriticVerdict:
    """Validate a raw LLM response string against the verdict schema.

    Accepts: a JSON object with `too_strong: bool` and `reason: str`.
    Rejects: non-JSON, non-object payloads, missing fields, or wrong types.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise CriticSchemaError(f"critic response is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise CriticSchemaError("critic verdict must be a JSON object")
    if "too_strong" not in data:
        raise CriticSchemaError("critic verdict missing required field 'too_strong'")
    if not isinstance(data["too_strong"], bool):
        raise CriticSchemaError("critic verdict field 'too_strong' must be a boolean")
    if "reason" not in data:
        raise CriticSchemaError("critic verdict missing required field 'reason'")
    if not isinstance(data["reason"], str):
        raise CriticSchemaError("critic verdict field 'reason' must be a string")
    return CriticVerdict(
        artifact_id=artifact_id,
        too_strong=data["too_strong"],
        reason=data["reason"],
        round=round,
    )


# Default fixture rule: any artifact whose id matches the canonical
# first-corroborator pattern `art_ev_prop_<N>_1` is flagged. This
# deterministically triggers remediation on the first artifact bound to
# each proposition, which is exactly what the smoke test needs to assert
# non-empty remediation activity. Remediated artifacts carry a different
# id pattern (see remediator._next_id), so the re-critic returns clean.
_FIRST_CORROBORATOR = re.compile(r"^art_ev_[A-Za-z0-9_]+_1$")


def _default_fixture_rule(artifact: Artifact) -> tuple[bool, str]:
    if _FIRST_CORROBORATOR.match(artifact.id):
        return True, "fixture-rule: first corroborator of a key proposition"
    return False, "fixture-rule: artifact is sufficiently weak in isolation"


@dataclass
class SmokingGunCritic:
    """Critiques artifacts emitted by the load-bearing pipeline stage.

    The verdict shape is the contract across fixture/real/recorded modes —
    every path lands in `parse_verdict` so schema validation is exercised
    uniformly. Tests inject `response_recorder` to feed recorded raw LLM
    responses through the same validator.
    """

    gateway: LLMGateway
    response_recorder: Callable[[Artifact], str] | None = None
    fixture_rule: Callable[[Artifact], tuple[bool, str]] = _default_fixture_rule

    def critique(
        self,
        artifact: Artifact,
        truth: CanonicalTruth,
        *,
        round: int = 0,
    ) -> CriticVerdict:
        prompt = self._build_prompt(artifact, truth)
        if self.response_recorder is not None:
            raw = self.response_recorder(artifact)
        elif self.gateway.mode == "fixture":
            too_strong, reason = self.fixture_rule(artifact)
            raw = json.dumps({"too_strong": too_strong, "reason": reason})
        else:  # pragma: no cover - real-mode is exercised by smoke runs only
            raw = self.gateway.complete(
                "critic", prompt, cache_key=f"critic::{artifact.id}::{round}"
            )
        return parse_verdict(artifact.id, raw, round=round)

    @staticmethod
    def _build_prompt(artifact: Artifact, truth: CanonicalTruth) -> str:
        return (
            "Decide whether this single artifact, on its own, solves the case or "
            "trivially proves any key proposition. Respond with JSON of shape "
            '{"too_strong": <bool>, "reason": <string>}.\n\n'
            f"Artifact id: {artifact.id}\n"
            f"Owner: {artifact.owner_id} / device {artifact.device_id} / profile {artifact.profile}\n"
            f"Bound propositions: {list(artifact.bound_proposition_ids)}\n"
            f"Truth outline: {truth.outline[:500]}\n"
        )
