"""Remediator unit tests.

Slice 8 ships the `split` and `dilute` transforms. These tests inject the
strategy (the chooser is a thin RNG wrapper, tested separately) and assert
the transform shape:

- split  ⇒  exactly 2 new artifacts, owner-distinct, same bound propositions,
            both with halved signal weight relative to the original
- dilute ⇒  exactly 1 new artifact, same owner & device, same bound
            propositions, halved signal weight, payload not byte-equal to
            the original (content was rewritten)

The transforms themselves are deterministic given the injected strategy;
the only nondeterminism is the random filename salt inside `write_email`,
which we do not assert against.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime

import pytest

from api.pipeline.llm_gateway import LLMGateway
from api.pipeline.persona_registry import default_registry
from api.pipeline.remediator import (
    DILUTE,
    SPLIT,
    SUPPORTED_STRATEGIES,
    RemediationError,
    choose_strategy,
    dilute,
    remediate,
    split,
)
from api.pipeline.types import Artifact

DISCLAIMER = "SYNTHETIC EVIDENCE — test"


def _seed_artifact(art_id: str = "art_ev_prop_1_1") -> Artifact:
    registry = default_registry()
    persona = registry.personas()[0]
    device = registry.devices_for(persona.id)[0]
    return Artifact(
        id=art_id,
        owner_id=persona.id,
        device_id=device.id,
        profile="email",
        filename="seed.eml",
        payload=b"From: x\nSubject: original-damning\n\nthe smoking gun text",
        sha256="0" * 64,
        acquisition_time=datetime(2024, 6, 3, 9, 0, tzinfo=UTC),
        bound_proposition_ids=("prop_1",),
        signal_weight=1.0,
    )


def test_choose_strategy_returns_only_supported_values():
    rng = random.Random(0)
    for _ in range(50):
        assert choose_strategy(rng) in SUPPORTED_STRATEGIES


def test_split_produces_two_owner_distinct_fragments():
    registry = default_registry()
    gateway = LLMGateway()
    art = _seed_artifact()
    out = split(art, registry, gateway=gateway, disclaimer=DISCLAIMER)
    assert out.strategy == SPLIT
    assert len(out.new_artifacts) == 2, "split must yield ≥ 2 fragments"
    a, b = out.new_artifacts
    assert a.owner_id != b.owner_id, "split fragments must be owner-distinct"
    # The first fragment keeps the original owner; the second uses an
    # alternate persona.
    assert a.owner_id == art.owner_id


def test_split_preserves_bound_propositions():
    registry = default_registry()
    gateway = LLMGateway()
    art = _seed_artifact()
    out = split(art, registry, gateway=gateway, disclaimer=DISCLAIMER)
    for new_art in out.new_artifacts:
        assert new_art.bound_proposition_ids == art.bound_proposition_ids


def test_split_halves_signal_weight():
    registry = default_registry()
    gateway = LLMGateway()
    art = _seed_artifact()
    out = split(art, registry, gateway=gateway, disclaimer=DISCLAIMER)
    for new_art in out.new_artifacts:
        assert new_art.signal_weight == pytest.approx(art.signal_weight * 0.5)


def test_split_emits_valid_rfc822_payloads():
    registry = default_registry()
    gateway = LLMGateway()
    art = _seed_artifact()
    out = split(art, registry, gateway=gateway, disclaimer=DISCLAIMER)
    for new_art in out.new_artifacts:
        assert new_art.payload.startswith(b"From: ")
        assert b"Subject: " in new_art.payload
        # SHA-256 must match the actual payload bytes.
        import hashlib

        assert hashlib.sha256(new_art.payload).hexdigest() == new_art.sha256


def test_dilute_produces_single_same_owner_artifact():
    registry = default_registry()
    gateway = LLMGateway()
    art = _seed_artifact()
    out = dilute(art, registry, gateway=gateway, disclaimer=DISCLAIMER)
    assert out.strategy == DILUTE
    assert len(out.new_artifacts) == 1
    (new_art,) = out.new_artifacts
    assert new_art.owner_id == art.owner_id
    assert new_art.device_id == art.device_id
    assert new_art.bound_proposition_ids == art.bound_proposition_ids
    assert new_art.signal_weight == pytest.approx(0.5)


def test_dilute_payload_differs_from_original():
    """Dilute rewrites the content; the new payload must not be byte-equal
    to the original's damning text."""
    registry = default_registry()
    gateway = LLMGateway()
    art = _seed_artifact()
    out = dilute(art, registry, gateway=gateway, disclaimer=DISCLAIMER)
    (new_art,) = out.new_artifacts
    assert new_art.payload != art.payload


def test_remediate_dispatches_by_strategy_name():
    registry = default_registry()
    gateway = LLMGateway()
    art = _seed_artifact()
    split_out = remediate(art, registry, strategy=SPLIT, gateway=gateway, disclaimer=DISCLAIMER)
    dilute_out = remediate(art, registry, strategy=DILUTE, gateway=gateway, disclaimer=DISCLAIMER)
    assert split_out.strategy == SPLIT
    assert dilute_out.strategy == DILUTE


def test_remediate_rejects_unknown_strategy():
    registry = default_registry()
    gateway = LLMGateway()
    art = _seed_artifact()
    with pytest.raises(RemediationError, match="unknown strategy"):
        remediate(art, registry, strategy="redact-relocate", gateway=gateway, disclaimer=DISCLAIMER)


def test_split_rejects_artifact_with_no_bound_propositions():
    registry = default_registry()
    gateway = LLMGateway()
    art = _seed_artifact()
    bare = Artifact(
        id=art.id,
        owner_id=art.owner_id,
        device_id=art.device_id,
        profile=art.profile,
        filename=art.filename,
        payload=art.payload,
        sha256=art.sha256,
        acquisition_time=art.acquisition_time,
        bound_proposition_ids=(),
        signal_weight=art.signal_weight,
    )
    with pytest.raises(RemediationError, match="no bound propositions"):
        split(bare, registry, gateway=gateway, disclaimer=DISCLAIMER)
