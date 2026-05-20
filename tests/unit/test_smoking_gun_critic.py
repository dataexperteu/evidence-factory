"""Smoking-Gun Critic contract tests.

The critic's contract is: a verdict is a JSON object with a boolean
`too_strong` and a string `reason`. These tests pin that schema against
recorded LLM responses (both well-formed and malformed) so subsequent
slices cannot widen the contract silently.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from api.pipeline.llm_gateway import LLMGateway
from api.pipeline.smoking_gun_critic import (
    CriticSchemaError,
    SmokingGunCritic,
    parse_verdict,
)
from api.pipeline.types import (
    Artifact,
    CanonicalTruth,
    Proposition,
    PropositionGraph,
)


def _artifact(art_id: str = "art_ev_prop_1_1") -> Artifact:
    return Artifact(
        id=art_id,
        owner_id="p_holmes",
        device_id="d_holmes_mail",
        profile="email",
        filename=f"{art_id}.eml",
        payload=b"",
        sha256="0" * 64,
        acquisition_time=datetime(2024, 6, 3, 9, 0, tzinfo=UTC),
        bound_proposition_ids=("prop_1",),
    )


def _truth() -> CanonicalTruth:
    return CanonicalTruth(
        outline="Holmes solved it.",
        graph=PropositionGraph(
            propositions=(Proposition(id="prop_1", text="Holmes was at Stoke Moran."),)
        ),
    )


def test_parse_verdict_accepts_well_formed_too_strong_true():
    raw = json.dumps({"too_strong": True, "reason": "names the murderer outright"})
    v = parse_verdict("art_1", raw)
    assert v.artifact_id == "art_1"
    assert v.too_strong is True
    assert v.reason == "names the murderer outright"
    assert v.round == 0


def test_parse_verdict_accepts_well_formed_too_strong_false():
    raw = json.dumps({"too_strong": False, "reason": "ambient context only"})
    v = parse_verdict("art_2", raw, round=2)
    assert v.too_strong is False
    assert v.round == 2


def test_parse_verdict_rejects_non_json():
    with pytest.raises(CriticSchemaError, match="not valid JSON"):
        parse_verdict("art_1", "not-json")


def test_parse_verdict_rejects_non_object():
    with pytest.raises(CriticSchemaError, match="JSON object"):
        parse_verdict("art_1", '[true, "why"]')


def test_parse_verdict_rejects_missing_too_strong():
    with pytest.raises(CriticSchemaError, match="too_strong"):
        parse_verdict("art_1", json.dumps({"reason": "x"}))


def test_parse_verdict_rejects_wrong_type_too_strong():
    with pytest.raises(CriticSchemaError, match="boolean"):
        parse_verdict("art_1", json.dumps({"too_strong": "yes", "reason": "x"}))


def test_parse_verdict_rejects_missing_reason():
    with pytest.raises(CriticSchemaError, match="reason"):
        parse_verdict("art_1", json.dumps({"too_strong": True}))


def test_parse_verdict_rejects_wrong_type_reason():
    with pytest.raises(CriticSchemaError, match="string"):
        parse_verdict("art_1", json.dumps({"too_strong": False, "reason": 7}))


def test_critic_uses_recorded_response_when_recorder_provided():
    """Contract test: a recorded LLM response flows through parse_verdict
    and yields the expected CriticVerdict."""
    canned = json.dumps({"too_strong": True, "reason": "recorded-too-strong"})
    gateway = LLMGateway()
    critic = SmokingGunCritic(gateway=gateway, response_recorder=lambda _a: canned)
    verdict = critic.critique(_artifact(), _truth())
    assert verdict.too_strong is True
    assert verdict.reason == "recorded-too-strong"


def test_critic_recorded_malformed_response_raises_schema_error():
    """If a recorded LLM response violates the schema, the critic surfaces
    a CriticSchemaError so contract failures are obvious."""
    bogus = json.dumps({"too_strong": "true"})  # wrong type, missing reason
    gateway = LLMGateway()
    critic = SmokingGunCritic(gateway=gateway, response_recorder=lambda _a: bogus)
    with pytest.raises(CriticSchemaError):
        critic.critique(_artifact(), _truth())


def test_fixture_rule_flags_first_corroborator_pattern():
    """The default fixture rule flags artifacts matching the canonical
    first-corroborator id, which lets the smoke test reliably trigger
    remediation without a live LLM."""
    gateway = LLMGateway()
    critic = SmokingGunCritic(gateway=gateway)
    flagged = critic.critique(_artifact("art_ev_prop_1_1"), _truth())
    assert flagged.too_strong is True
    not_flagged = critic.critique(_artifact("art_ev_prop_1_2"), _truth())
    assert not_flagged.too_strong is False
    remediated = critic.critique(_artifact("art_ev_prop_1_1__split_a_abc123"), _truth())
    assert remediated.too_strong is False
