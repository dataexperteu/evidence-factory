"""Smoking-Gun Critic — verdict-schema contract + critique integration.

These pin the verdict schema against recorded LLM responses (PRD: LLM-backed
modules get contract tests, not exact-output tests) and check that the critic
attaches the artifact id and flags a full-strength artifact in fixture mode.
"""

from datetime import UTC, datetime

import pytest

from api.pipeline.critic import (
    CriticVerdict,
    SmokingGunCritic,
    VerdictSchemaError,
    parse_verdict,
)
from api.pipeline.llm_gateway import SMOKING_GUN_WEIGHT_THRESHOLD, LLMGateway
from api.pipeline.types import Artifact, CanonicalTruth, Proposition, PropositionGraph


def _artifact(weight):
    return Artifact(
        id="art_1",
        owner_id="p_holmes",
        device_id="d_holmes_mail",
        profile="email",
        filename="art_1.eml",
        payload=b"Subject: x\n\nHolmes was at the scene at midnight.",
        sha256="0" * 64,
        acquisition_time=datetime(2024, 1, 1, tzinfo=UTC),
        bound_proposition_ids=("prop_1",),
        signal_weight=weight,
    )


def _truth():
    return CanonicalTruth(
        outline="A test case.",
        graph=PropositionGraph(propositions=(Proposition(id="prop_1", text="X happened"),)),
    )


# -- parse_verdict contract -------------------------------------------------


def test_parse_verdict_accepts_well_formed_too_strong():
    v = parse_verdict('{"too_strong": true, "reason": "proves it alone"}', "art_7")
    assert v == CriticVerdict(artifact_id="art_7", too_strong=True, reason="proves it alone")


def test_parse_verdict_accepts_not_too_strong():
    v = parse_verdict('{"too_strong": false, "reason": "merely corroborative"}', "art_8")
    assert v.too_strong is False
    assert v.artifact_id == "art_8"


def test_parse_verdict_defaults_missing_reason_to_empty_string():
    v = parse_verdict('{"too_strong": true}', "art_9")
    assert v.reason == ""


def test_parse_verdict_rejects_non_json():
    with pytest.raises(VerdictSchemaError):
        parse_verdict("not json at all", "art_1")


def test_parse_verdict_rejects_non_object():
    with pytest.raises(VerdictSchemaError):
        parse_verdict("[true]", "art_1")


def test_parse_verdict_rejects_missing_too_strong():
    with pytest.raises(VerdictSchemaError):
        parse_verdict('{"reason": "no verdict"}', "art_1")


def test_parse_verdict_rejects_non_boolean_too_strong():
    with pytest.raises(VerdictSchemaError):
        parse_verdict('{"too_strong": "yes"}', "art_1")


def test_parse_verdict_rejects_non_string_reason():
    with pytest.raises(VerdictSchemaError):
        parse_verdict('{"too_strong": true, "reason": 5}', "art_1")


def test_verdict_is_json_serialisable():
    v = CriticVerdict(artifact_id="a", too_strong=True, reason="r")
    assert v.to_json_serialisable() == {
        "artifact_id": "a",
        "too_strong": True,
        "reason": "r",
    }


# -- critique integration (fixture gateway) ---------------------------------


def test_critique_flags_full_strength_artifact():
    critic = SmokingGunCritic(LLMGateway(mode="fixture"))
    verdict = critic.critique(_artifact(SMOKING_GUN_WEIGHT_THRESHOLD), _truth())
    assert verdict.too_strong is True
    assert verdict.artifact_id == "art_1"


def test_critique_passes_diluted_artifact():
    critic = SmokingGunCritic(LLMGateway(mode="fixture"))
    verdict = critic.critique(_artifact(SMOKING_GUN_WEIGHT_THRESHOLD / 2), _truth())
    assert verdict.too_strong is False
