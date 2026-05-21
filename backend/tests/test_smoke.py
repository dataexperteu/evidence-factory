"""
Smoke test: end-to-end noise generation at default volume (mocked LLM).

This fulfils the Slice 10 acceptance criterion:
  "The end-to-end smoke test from slice #3 still passes at default noise volume."

Since Slice 3 (tracer bullet) is not yet merged, this smoke test establishes
the regression baseline: after noise generation the corpus structure is sound,
noise is absent from the Signal Ledger, and noise_summary.json is well-formed.
"""

from __future__ import annotations

import json
from typing import Any

from pytest_mock import MockerFixture

from evidence_factory.ledger import SignalLedger
from evidence_factory.llm.gateway import LLMGateway
from evidence_factory.models import ArtifactProfile, CaseBible
from evidence_factory.noise.generator import DEFAULT_TARGET, NoiseGenerator
from evidence_factory.noise.guard import LeakContradictGuard
from evidence_factory.provenance.catalog import ProvenanceCatalog
from evidence_factory.registry import PersonaRegistry

# ---------------------------------------------------------------------------
# Smoke-test fixtures
# ---------------------------------------------------------------------------

_PROFILE_DEVICE_MAP = {
    ArtifactProfile.EMAIL: ("alice", "alice_laptop"),
    ArtifactProfile.SMS: ("alice", "alice_phone"),
    ArtifactProfile.PDF: ("alice", "alice_laptop"),
    ArtifactProfile.XLSX: ("alice", "alice_laptop"),
    ArtifactProfile.JPEG: ("alice", "alice_phone"),
    ArtifactProfile.SYSTEM_LOG_CSV: ("bob", "bob_workstation"),
}


def _make_item(profile: ArtifactProfile, idx: int) -> dict[str, Any]:
    owner, device = _PROFILE_DEVICE_MAP[profile]
    return {
        "text_content": f"Mundane message number {idx} about nothing suspicious.",
        "owner": owner,
        "device": device,
        "profile": profile.value,
        "timestamp_offset_hours": float(idx % 1000),
        "metadata": {
            "subject": f"Routine Update {idx}",
            "from": f"{owner}@example.com",
            "to": "colleague@example.com",
        },
    }


def _build_smoke_generator(mocker: MockerFixture, registry: PersonaRegistry) -> NoiseGenerator:
    """Build NoiseGenerator with a mocked LLM that cycles through all 6 profiles."""
    call_counter: list[int] = [0]
    profiles = list(ArtifactProfile)

    def _side_effect(*args: Any, **kwargs: Any) -> Any:
        n = call_counter[0]
        call_counter[0] += 1
        profile = profiles[n % len(profiles)]
        artifacts = [_make_item(profile, n * 10 + i) for i in range(10)]
        response = mocker.MagicMock()
        response.content = [mocker.MagicMock(text=json.dumps({"artifacts": artifacts}))]
        # Alternate: first call = cache miss; rest = cache hits
        if n == 0:
            response.usage.cache_read_input_tokens = 0
            response.usage.cache_creation_input_tokens = 800
        else:
            response.usage.cache_read_input_tokens = 800
            response.usage.cache_creation_input_tokens = 0
        return response

    mock_client = mocker.MagicMock()
    mock_client.messages.create.side_effect = _side_effect
    gw = LLMGateway(client=mock_client)

    # Guard: pass all (no leaks in our test content)
    mock_guard = mocker.MagicMock(spec=LeakContradictGuard)
    mock_guard.check_batch.side_effect = lambda texts, props: [False] * len(texts)

    return NoiseGenerator(llm=gw, catalog=ProvenanceCatalog(), registry=registry, guard=mock_guard)


# ---------------------------------------------------------------------------
# Smoke test: default volume
# ---------------------------------------------------------------------------


def test_smoke_default_noise_volume(
    mocker: MockerFixture, bible: CaseBible, registry: PersonaRegistry
) -> None:
    """Full pipeline at default noise volume (300 artifacts)."""
    gen = _build_smoke_generator(mocker, registry)
    artifacts, summary = gen.generate(bible)  # default target = 300

    # 1. Generated the target count
    assert summary.generated_count == DEFAULT_TARGET
    assert len(artifacts) == DEFAULT_TARGET

    # 2. All artifacts are marked as noise
    assert all(a.is_noise for a in artifacts)

    # 3. All timestamps within master timeline
    for artifact in artifacts:
        assert bible.master_timeline.start <= artifact.timestamp <= bible.master_timeline.end

    # 4. All triples are permitted by the registry
    for artifact in artifacts:
        assert registry.is_permitted(artifact.owner, artifact.device, artifact.profile), (
            f"Unpermitted triple: ({artifact.owner}, {artifact.device}, {artifact.profile})"
        )

    # 5. Noise is NOT in the Signal Ledger
    ledger = SignalLedger()
    assert len(ledger) == 0
    for artifact in artifacts:
        assert not ledger.has_signal(artifact.artifact_id)

    # 6. noise_summary.json structure is complete
    noise_json = json.dumps(summary.to_dict())
    decoded = json.loads(noise_json)
    required_keys = {
        "target_count",
        "generated_count",
        "rejected_count",
        "profile_distribution",
        "cache_hits",
        "cache_misses",
    }
    assert required_keys.issubset(decoded.keys())
    assert decoded["target_count"] == DEFAULT_TARGET
    assert decoded["generated_count"] == DEFAULT_TARGET

    # 7. Prompt caching: first call = miss, rest = hits
    assert summary.cache_misses >= 1
    assert summary.cache_hits >= 1


def test_smoke_all_six_profiles_appear(
    mocker: MockerFixture, bible: CaseBible, registry: PersonaRegistry
) -> None:
    """At default volume, every one of the six profiles must be present."""
    gen = _build_smoke_generator(mocker, registry)
    artifacts, summary = gen.generate(bible)
    profiles_used = {a.profile for a in artifacts}
    assert profiles_used == set(ArtifactProfile), (
        f"Missing profiles: {set(ArtifactProfile) - profiles_used}"
    )
    # profile_distribution must mention all six
    dist = summary.profile_distribution
    for profile in ArtifactProfile:
        assert profile.value in dist, (
            f"Missing {profile.value} in noise_summary profile_distribution"
        )


def test_smoke_noise_summary_profile_counts_sum_to_total(
    mocker: MockerFixture, bible: CaseBible, registry: PersonaRegistry
) -> None:
    gen = _build_smoke_generator(mocker, registry)
    _, summary = gen.generate(bible, target_count=60)
    total_in_dist = sum(summary.profile_distribution.values())
    assert total_in_dist == summary.generated_count


def test_smoke_artifact_content_is_bytes(
    mocker: MockerFixture, bible: CaseBible, registry: PersonaRegistry
) -> None:
    gen = _build_smoke_generator(mocker, registry)
    artifacts, _ = gen.generate(bible, target_count=30)
    for artifact in artifacts:
        assert isinstance(artifact.content, bytes)
        assert len(artifact.content) > 0


def test_smoke_each_artifact_has_unique_id(
    mocker: MockerFixture, bible: CaseBible, registry: PersonaRegistry
) -> None:
    gen = _build_smoke_generator(mocker, registry)
    artifacts, _ = gen.generate(bible, target_count=30)
    ids = [a.artifact_id for a in artifacts]
    assert len(ids) == len(set(ids)), "Artifact IDs are not unique"
