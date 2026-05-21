"""Signal Ledger — corroboration count + owner-distinct independence."""

from datetime import UTC, datetime

from api.pipeline.signal_ledger import SignalLedger
from api.pipeline.types import Artifact


def _art(art_id: str, owner: str, props: tuple[str, ...]) -> Artifact:
    return Artifact(
        id=art_id,
        owner_id=owner,
        device_id=f"d_{owner}",
        profile="email",
        filename=f"{art_id}.eml",
        payload=b"",
        sha256="0" * 64,
        acquisition_time=datetime(2024, 1, 1, tzinfo=UTC),
        bound_proposition_ids=props,
    )


def test_empty_ledger_has_no_corroborators():
    led = SignalLedger()
    assert led.corroborators_for("prop_1") == []
    assert led.distinct_owners_for("prop_1") == set()


def test_records_and_counts_corroborators():
    led = SignalLedger()
    led.record(_art("a1", "p_holmes", ("prop_1",)))
    led.record(_art("a2", "p_holmes", ("prop_1",)))  # same owner, doesn't add owner-distinct
    led.record(_art("a3", "p_watson", ("prop_1",)))
    led.record(_art("a4", "p_holmes", ("prop_2",)))

    assert len(led.corroborators_for("prop_1")) == 3
    assert led.distinct_owners_for("prop_1") == {"p_holmes", "p_watson"}
    assert led.distinct_owners_for("prop_2") == {"p_holmes"}
    assert led.distinct_owners_for("prop_missing") == set()


def test_json_serialisable_round_trips_fields():
    led = SignalLedger()
    led.record(_art("a1", "p_holmes", ("prop_1", "prop_2")))
    payload = led.to_json_serialisable()
    assert payload == [
        {
            "artifact_id": "a1",
            "owner_id": "p_holmes",
            "device_id": "d_p_holmes",
            "profile": "email",
            "proposition_ids": ["prop_1", "prop_2"],
            "signal_weight": 1.0,
        }
    ]


def test_replace_entries_rebuilds_corroboration_but_keeps_critic_records():
    led = SignalLedger()
    led.record(_art("old", "p_holmes", ("prop_1",)))
    led.record_critic_verdict({"artifact_id": "old", "too_strong": True, "reason": "r"})
    led.record_remediation({"flagged_artifact_id": "old", "strategy": "split"})

    led.replace_entries([_art("new", "p_watson", ("prop_1",))])

    assert {e.artifact_id for e in led.entries()} == {"new"}
    assert led.distinct_owners_for("prop_1") == {"p_watson"}
    # Verdicts and remediation activity survive the rebuild.
    assert led.critic_verdicts() == [{"artifact_id": "old", "too_strong": True, "reason": "r"}]
    assert led.remediations() == [{"flagged_artifact_id": "old", "strategy": "split"}]
