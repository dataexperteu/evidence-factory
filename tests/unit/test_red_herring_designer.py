"""Red-Herring & Breaker Designer — contract + invariant tests.

These assert the Designer's output-schema validity (every red herring has a
proposition, >= 1 supporting artifact, and an owner-distinct breaker bundle of
>= 2 artifacts) and the refuted-by-construction invariants:

- the breaker bundle refutes the red herring in aggregate by the configured
  margin;
- no breaker artifact individually convicts (each passes the Smoking-Gun
  Critic, i.e. stays below the bar);
- a demote-scheduled red herring is completed (its support is referenced, a
  breaker bundle is built);
- a baseline red herring is synthesised when nothing is scheduled.
"""

from datetime import UTC, datetime

from api.pipeline.closure_verifier import verify_closure
from api.pipeline.critic import SmokingGunCritic
from api.pipeline.llm_gateway import SMOKING_GUN_WEIGHT_THRESHOLD, LLMGateway
from api.pipeline.persona_registry import default_registry
from api.pipeline.red_herring_designer import DesignerSettings, design_red_herrings
from api.pipeline.remediator import demote
from api.pipeline.signal_ledger import SignalLedger
from api.pipeline.types import (
    CanonicalTruth,
    Proposition,
    PropositionGraph,
)
from api.provenance.email_profile import EmailBrief, write_email

DISCLAIMER = "SYNTHETIC"
BASE = datetime(2024, 6, 20, 9, 0, tzinfo=UTC)


def _critic() -> SmokingGunCritic:
    return SmokingGunCritic(LLMGateway(mode="fixture"))


def _truth() -> CanonicalTruth:
    return CanonicalTruth(
        outline="A test case about a midnight deed at Stoke Moran.",
        graph=PropositionGraph(
            propositions=(
                Proposition(id="prop_1", text="The deed happened at midnight."),
                Proposition(id="prop_2", text="Holmes was present."),
            )
        ),
    )


def _flagged_artifact(art_id="art_x", owner_id="p_holmes"):
    from api.pipeline.types import Artifact

    registry = default_registry()
    persona = registry.get_persona(owner_id)
    device = registry.devices_for(owner_id)[0]
    other = next(p for p in registry.personas() if p.id != owner_id)
    brief = EmailBrief(
        sender_name=persona.display_name,
        sender_address=persona.email_address,
        recipients=((other.display_name, other.email_address),),
        subject="the whole story",
        body="Holmes confirms he was at Stoke Moran at midnight and saw the deed done.",
        sent_at=BASE,
    )
    written = write_email(brief, disclaimer=DISCLAIMER)
    return Artifact(
        id=art_id,
        owner_id=owner_id,
        device_id=device.id,
        profile="email",
        filename=written.filename,
        payload=written.payload,
        sha256=written.sha256,
        acquisition_time=BASE,
        bound_proposition_ids=("prop_1",),
        signal_weight=1.0,
    )


# -- baseline (nothing scheduled) -------------------------------------------


def test_designs_at_least_one_baseline_red_herring_when_nothing_scheduled():
    red_herrings, new_artifacts = design_red_herrings(
        _truth(),
        default_registry(),
        critic=_critic(),
        disclaimer=DISCLAIMER,
        base_time=BASE,
    )
    assert len(red_herrings) >= 1
    rh = red_herrings[0]
    assert rh.proposition.id and rh.proposition.text
    assert len(rh.supporting) >= 1
    assert len(rh.breakers) >= 2
    # Baseline support + breakers are all new corpus artifacts.
    new_ids = {a.id for a in new_artifacts}
    for art in (*rh.supporting, *rh.breakers):
        assert art.id in new_ids


def test_baseline_breaker_bundle_is_owner_distinct():
    red_herrings, _ = design_red_herrings(
        _truth(), default_registry(), critic=_critic(), disclaimer=DISCLAIMER, base_time=BASE
    )
    rh = red_herrings[0]
    owners = {b.owner_id for b in rh.breakers}
    assert len(owners) == len(rh.breakers)
    assert len(owners) >= 2


def test_every_breaker_passes_the_smoking_gun_critic():
    critic = _critic()
    truth = _truth()
    red_herrings, _ = design_red_herrings(
        truth, default_registry(), critic=critic, disclaimer=DISCLAIMER, base_time=BASE
    )
    for rh in red_herrings:
        for breaker in rh.breakers:
            assert breaker.signal_weight < SMOKING_GUN_WEIGHT_THRESHOLD
            assert critic.critique(breaker, truth).too_strong is False


def test_breaker_bundle_refutes_by_configured_margin():
    settings = DesignerSettings(breaker_margin=0.5)
    red_herrings, _ = design_red_herrings(
        _truth(),
        default_registry(),
        critic=_critic(),
        disclaimer=DISCLAIMER,
        base_time=BASE,
        settings=settings,
    )
    for rh in red_herrings:
        assert rh.breaker_signal >= rh.support_signal * (1.0 + settings.breaker_margin)


def test_designer_output_satisfies_closure_red_herring_invariant():
    graph = _truth().graph
    red_herrings, _ = design_red_herrings(
        _truth(), default_registry(), critic=_critic(), disclaimer=DISCLAIMER, base_time=BASE
    )
    led = SignalLedger()
    for prop in graph.propositions:
        for owner in ("o1", "o2"):
            from api.pipeline.types import Artifact

            led.record(
                Artifact(
                    id=f"a_{owner}_{prop.id}",
                    owner_id=owner,
                    device_id=f"d_{owner}",
                    profile="email",
                    filename="x.eml",
                    payload=b"",
                    sha256="0" * 64,
                    acquisition_time=BASE,
                    bound_proposition_ids=(prop.id,),
                )
            )
    result = verify_closure(graph, led, min_owner_distinct=2, red_herrings=red_herrings)
    assert result.ok is True
    assert result.red_herring_gaps == []


# -- scheduled (demote origin) ----------------------------------------------


def test_completes_scheduled_red_herring_referencing_existing_support():
    art = _flagged_artifact()
    result = demote(art, default_registry(), disclaimer=DISCLAIMER)
    support = result.red_herring_support
    scheduled = [
        {
            "proposition_id": result.false_proposition.id,
            "proposition_text": result.false_proposition.text,
            "support_artifact_ids": [support.id],
            "origin_artifact_id": art.id,
        }
    ]
    red_herrings, new_artifacts = design_red_herrings(
        _truth(),
        default_registry(),
        critic=_critic(),
        disclaimer=DISCLAIMER,
        base_time=BASE,
        scheduled=scheduled,
        existing_artifacts=[support],
        settings=DesignerSettings(min_red_herrings=1),
    )
    # The scheduled red herring is present and references the pre-existing
    # support artifact (not re-created), with a fresh breaker bundle.
    sched_rh = next(r for r in red_herrings if r.proposition.id == result.false_proposition.id)
    assert sched_rh.supporting[0].id == support.id
    assert len(sched_rh.breakers) >= 2
    # Support artifact was already in the corpus, so it is not in new_artifacts.
    assert support.id not in {a.id for a in new_artifacts}
    # Breakers are new.
    for breaker in sched_rh.breakers:
        assert breaker.id in {a.id for a in new_artifacts}


def test_designer_records_breaker_critic_verdicts_on_ledger():
    led = SignalLedger()
    design_red_herrings(
        _truth(),
        default_registry(),
        critic=_critic(),
        disclaimer=DISCLAIMER,
        base_time=BASE,
        ledger=led,
    )
    # Each breaker was critiqued; verdicts recorded and all clear the bar.
    verdicts = led.critic_verdicts()
    assert verdicts
    assert all(v["too_strong"] is False for v in verdicts)
