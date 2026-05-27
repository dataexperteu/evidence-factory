"""NoiseGenerator tests (fixture-mode gateway, real registry + guard)."""

from __future__ import annotations

from datetime import UTC, datetime

from api.pipeline.llm_gateway import LLMGateway
from api.pipeline.noise_generator import BATCH_SIZE, DEFAULT_TARGET, MAX_COUNT, NoiseGenerator
from api.pipeline.noise_guard import LeakContradictGuard
from api.pipeline.persona_registry import default_registry

PROPOSITIONS = [
    "Alice transferred $50,000 to the Cayman Islands account on January 15, 2024",
    "Bob deleted the audit logs at 3:47 AM on January 16, 2024",
]
OUTLINE = "A fictional corporate fraud investigation."
START = datetime(2024, 1, 1, tzinfo=UTC)
END = datetime(2024, 3, 1, tzinfo=UTC)
DISCLAIMER = "SYNTHETIC EVIDENCE — generated for demonstration."


def _generator(seed: int = 7) -> NoiseGenerator:
    gateway = LLMGateway(mode="fixture", rng_seed=seed)
    return NoiseGenerator(gateway, default_registry(), LeakContradictGuard(gateway))


def _generate(gen: NoiseGenerator, **kw):  # type: ignore[no-untyped-def]
    return gen.generate(
        propositions=PROPOSITIONS,
        outline=OUTLINE,
        timeline_start=START,
        timeline_end=END,
        disclaimer=DISCLAIMER,
        **kw,
    )


def test_default_target_is_300() -> None:
    assert DEFAULT_TARGET == 300


def test_max_count_is_1000() -> None:
    assert MAX_COUNT == 1000


def test_batch_size_at_least_10() -> None:
    assert BATCH_SIZE >= 10


def test_generates_target_count() -> None:
    artifacts, summary = _generate(_generator(), target_count=30)
    assert summary.generated_count == 30
    assert len(artifacts) == 30


def test_respects_max_count() -> None:
    _, summary = _generate(_generator(), target_count=500, max_count=20)
    assert summary.generated_count <= 20
    assert summary.target_count == 20


def test_only_permitted_triples() -> None:
    registry = default_registry()
    artifacts, _ = _generate(_generator(), target_count=40)
    for art in artifacts:
        assert registry.is_permitted(art.owner_id, art.device_id, art.profile), (
            f"impermissible triple: {art.owner_id}/{art.device_id}/{art.profile}"
        )


def test_timestamps_within_timeline() -> None:
    artifacts, _ = _generate(_generator(), target_count=40)
    for art in artifacts:
        assert START <= art.acquisition_time <= END, (
            f"timestamp {art.acquisition_time} out of range"
        )


def test_noise_carries_no_bound_propositions() -> None:
    artifacts, _ = _generate(_generator(), target_count=30)
    assert artifacts
    for art in artifacts:
        assert art.bound_proposition_ids == ()
        assert art.signal_weight == 0.0


def test_all_profiles_represented() -> None:
    # Multi-profile devices expand the triple list to 33 entries (31 persona
    # device-profile combos + 2 system devices). With BATCH_SIZE=10 each
    # iteration, we need >33 iterations = >330 artifacts to cycle through all
    # triples including the two system_log_csv devices at the end of the list.
    artifacts, summary = _generate(_generator(), target_count=400)
    used = {a.profile for a in artifacts}
    expected = {"email", "pdf", "sms", "jpeg", "xlsx_ledger", "system_log_csv"}
    assert used == expected, f"missing profiles: {expected - used}"
    assert set(summary.profile_distribution) == expected


def test_summary_has_required_fields() -> None:
    _, summary = _generate(_generator(), target_count=20)
    d = summary.to_dict()
    for key in (
        "target_count",
        "generated_count",
        "rejected_count",
        "profile_distribution",
        "cache_hits",
        "cache_misses",
    ):
        assert key in d
    assert isinstance(d["profile_distribution"], dict)


def test_cache_hits_logged_across_batches() -> None:
    # The persona/timeline context re-uses one cache key; every batch after the
    # first registers a hit. 30 artifacts => multiple batches => >= 1 hit.
    _, summary = _generate(_generator(), target_count=30)
    assert summary.cache_hits >= 1
    assert summary.cache_misses >= 1


def test_zero_target_produces_nothing() -> None:
    artifacts, summary = _generate(_generator(), target_count=0)
    assert artifacts == []
    assert summary.generated_count == 0


def test_two_runs_differ() -> None:
    a1, _ = _generate(_generator(seed=1), target_count=20)
    a2, _ = _generate(_generator(seed=2), target_count=20)
    assert [a.payload for a in a1] != [a.payload for a in a2]


def test_rejected_noise_is_regenerated() -> None:
    """A guard that rejects the first batch still reaches the target."""
    gateway = LLMGateway(mode="fixture", rng_seed=3)

    class _FirstBatchRejectingGuard(LeakContradictGuard):
        calls = 0

        def check_batch(self, texts: list[str], props: list[str]) -> list[bool]:
            type(self).calls += 1
            if type(self).calls == 1:
                return [True] * len(texts)
            return [False] * len(texts)

    guard = _FirstBatchRejectingGuard(gateway)
    gen = NoiseGenerator(gateway, default_registry(), guard)
    artifacts, summary = _generate(gen, target_count=10)
    assert summary.rejected_count >= BATCH_SIZE
    assert len(artifacts) == 10
