"""Remediator — split & dilute transform shapes given an injected strategy.

PRD test scope: split yields N weaker, owner-distinct fragments whose combined
signal preserves corroboration; dilute yields a single weaker artifact that
preserves the payload-length envelope. Exact prose is never asserted.
"""

import random
from datetime import UTC, datetime
from email.parser import BytesParser
from email.policy import default as default_policy

from api.pipeline.llm_gateway import SMOKING_GUN_WEIGHT_THRESHOLD
from api.pipeline.persona_registry import default_registry
from api.pipeline.remediator import (
    DILUTE_FACTOR,
    FixedStrategySelector,
    RandomStrategySelector,
    dilute,
    remediate,
    split,
)
from api.pipeline.types import Artifact
from api.provenance.email_profile import EmailBrief, write_email

DISCLAIMER = "SYNTHETIC — generated for demonstration."
DAMNING = "Holmes confirms he was at Stoke Moran at midnight and saw the deed done."


def _artifact(owner_id="p_holmes", props=("prop_1",), weight=1.0):
    registry = default_registry()
    persona = registry.get_persona(owner_id)
    device = registry.devices_for(owner_id)[0]
    other = next(p for p in registry.personas() if p.id != owner_id)
    brief = EmailBrief(
        sender_name=persona.display_name,
        sender_address=persona.email_address,
        recipients=((other.display_name, other.email_address),),
        subject="the whole story",
        body=DAMNING,
        sent_at=datetime(2024, 6, 3, 9, 0, tzinfo=UTC),
    )
    written = write_email(brief, disclaimer=DISCLAIMER)
    return Artifact(
        id="art_x",
        owner_id=owner_id,
        device_id=device.id,
        profile="email",
        filename=written.filename,
        payload=written.payload,
        sha256=written.sha256,
        acquisition_time=datetime(2024, 6, 3, 9, 0, tzinfo=UTC),
        bound_proposition_ids=props,
        signal_weight=weight,
    )


def _body(payload):
    msg = BytesParser(policy=default_policy).parsebytes(payload)
    return msg.get_content()


# -- dilute -----------------------------------------------------------------


def test_dilute_produces_single_weaker_same_owner_artifact():
    art = _artifact(weight=1.0)
    out = dilute(art, default_registry(), disclaimer=DISCLAIMER)
    assert len(out) == 1
    diluted = out[0]
    assert diluted.owner_id == art.owner_id
    assert diluted.device_id == art.device_id
    assert diluted.bound_proposition_ids == art.bound_proposition_ids
    assert diluted.signal_weight == art.signal_weight * DILUTE_FACTOR
    assert diluted.signal_weight < SMOKING_GUN_WEIGHT_THRESHOLD


def test_dilute_preserves_payload_length_envelope_and_buries_span():
    art = _artifact(weight=1.0)
    diluted = dilute(art, default_registry(), disclaimer=DISCLAIMER)[0]
    original_body = _body(art.payload)
    new_body = _body(diluted.payload)
    # The damning span is buried in surrounding mundane text, not deleted.
    assert DAMNING in new_body
    # Length envelope preserved: the diluted body stays within a small factor.
    assert len(original_body) <= len(new_body) <= len(original_body) * 4


def test_dilute_output_is_valid_rfc822():
    diluted = dilute(_artifact(), default_registry(), disclaimer=DISCLAIMER)[0]
    msg = BytesParser(policy=default_policy).parsebytes(diluted.payload)
    assert msg["Subject"]
    assert msg["From"]
    assert msg["To"]


# -- split ------------------------------------------------------------------


def test_split_yields_at_least_two_owner_distinct_fragments():
    art = _artifact(weight=1.0)
    fragments = split(art, default_registry(), disclaimer=DISCLAIMER)
    assert len(fragments) >= 2
    owners = [f.owner_id for f in fragments]
    assert len(set(owners)) == len(owners)  # owner-distinct


def test_split_preserves_corroboration_and_combined_weight():
    art = _artifact(owner_id="p_holmes", props=("prop_1",), weight=1.0)
    fragments = split(art, default_registry(), disclaimer=DISCLAIMER)
    # Original owner kept so the proposition never loses an existing corroborator.
    assert art.owner_id in {f.owner_id for f in fragments}
    # Combined signal preserved.
    assert sum(f.signal_weight for f in fragments) == art.signal_weight
    # Every fragment stays bound to the proposition and below the smoking-gun bar.
    for f in fragments:
        assert f.bound_proposition_ids == art.bound_proposition_ids
        assert f.signal_weight < SMOKING_GUN_WEIGHT_THRESHOLD


def test_split_fragments_use_registry_devices_and_are_rfc822():
    registry = default_registry()
    fragments = split(_artifact(), registry, disclaimer=DISCLAIMER)
    for f in fragments:
        device = registry.get_device(f.device_id)
        assert device.owner_id == f.owner_id
        msg = BytesParser(policy=default_policy).parsebytes(f.payload)
        assert msg["Subject"] and msg["From"] and msg["To"]


# -- dispatch + selectors ---------------------------------------------------


def test_remediate_dispatches_on_strategy():
    art = _artifact()
    registry = default_registry()
    assert len(remediate(art, "dilute", registry, disclaimer=DISCLAIMER)) == 1
    assert len(remediate(art, "split", registry, disclaimer=DISCLAIMER)) >= 2


def test_fixed_strategy_selector_returns_its_strategy():
    art = _artifact()
    assert FixedStrategySelector("split").choose(art) == "split"
    assert FixedStrategySelector("dilute").choose(art) == "dilute"


def test_random_strategy_selector_is_seeded_and_only_yields_known_strategies():
    selector = RandomStrategySelector(random.Random(1234))
    art = _artifact()
    picks = {selector.choose(art) for _ in range(20)}
    assert picks <= {"split", "dilute"}
