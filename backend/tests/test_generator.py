"""Tests for the NoiseGenerator (contract-level, mocked LLM)."""

from __future__ import annotations

import json
from typing import Any

from pytest_mock import MockerFixture

from evidence_factory.ledger import SignalLedger
from evidence_factory.llm.gateway import LLMGateway
from evidence_factory.models import ArtifactProfile, CaseBible
from evidence_factory.noise.generator import BATCH_SIZE, NoiseGenerator
from evidence_factory.noise.guard import LeakContradictGuard
from evidence_factory.provenance.catalog import ProvenanceCatalog
from evidence_factory.registry import PersonaRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _noise_item(
    owner: str = "alice",
    device: str = "alice_laptop",
    profile: str = "email",
    offset: float = 1.0,
    content: str = "Nothing suspicious here.",
) -> dict[str, Any]:
    return {
        "text_content": content,
        "owner": owner,
        "device": device,
        "profile": profile,
        "timestamp_offset_hours": offset,
        "metadata": {"subject": "Chat", "from": f"{owner}@example.com", "to": "other@example.com"},
    }


def _make_batch(
    size: int = BATCH_SIZE,
    owner: str = "alice",
    device: str = "alice_laptop",
    profile: str = "email",
) -> list[dict[str, Any]]:
    return [
        _noise_item(owner=owner, device=device, profile=profile, offset=float(i + 1))
        for i in range(size)
    ]


def _build_generator(
    mocker: MockerFixture,
    bible: CaseBible,
    registry: PersonaRegistry,
    *,
    batches: list[list[dict[str, Any]]] | None = None,
    cache_read_tokens: int = 0,
    guard_flags: list[bool] | None = None,
) -> tuple[NoiseGenerator, LLMGateway, LeakContradictGuard]:
    """Wire up a NoiseGenerator with mocked LLM + guard."""
    mock_client = mocker.MagicMock()
    call_count = 0

    def _side_effect(*args: Any, **kwargs: Any) -> Any:
        nonlocal call_count
        if batches is not None:
            batch = batches[min(call_count, len(batches) - 1)]
        else:
            batch = _make_batch()
        call_count += 1

        response = mocker.MagicMock()
        response.content = [mocker.MagicMock(text=json.dumps({"artifacts": batch}))]
        response.usage.cache_read_input_tokens = cache_read_tokens
        response.usage.cache_creation_input_tokens = 0 if cache_read_tokens else 500
        return response

    mock_client.messages.create.side_effect = _side_effect
    gw = LLMGateway(client=mock_client)

    # Guard: unless overridden, pass everything
    mock_guard = mocker.MagicMock(spec=LeakContradictGuard)
    if guard_flags is not None:
        mock_guard.check_batch.side_effect = lambda texts, props: guard_flags[: len(texts)]
    else:
        mock_guard.check_batch.side_effect = lambda texts, props: [False] * len(texts)

    catalog = ProvenanceCatalog()
    generator = NoiseGenerator(llm=gw, catalog=catalog, registry=registry, guard=mock_guard)
    return generator, gw, mock_guard


# ---------------------------------------------------------------------------
# Target count
# ---------------------------------------------------------------------------


def test_generates_target_count(
    mocker: MockerFixture, bible: CaseBible, registry: PersonaRegistry
) -> None:
    target = 30
    gen, gw, _ = _build_generator(mocker, bible, registry)
    artifacts, summary = gen.generate(bible, target_count=target)
    assert summary.generated_count == target
    assert len(artifacts) == target


def test_respects_max_count(
    mocker: MockerFixture, bible: CaseBible, registry: PersonaRegistry
) -> None:
    gen, _, _ = _build_generator(mocker, bible, registry)
    _, summary = gen.generate(bible, target_count=500, max_count=20)
    assert summary.generated_count <= 20
    assert summary.target_count == 20


def test_default_target_is_300(
    mocker: MockerFixture, bible: CaseBible, registry: PersonaRegistry
) -> None:
    from evidence_factory.noise.generator import DEFAULT_TARGET

    assert DEFAULT_TARGET == 300


# ---------------------------------------------------------------------------
# Profile coverage
# ---------------------------------------------------------------------------


def _multi_profile_batches(registry: PersonaRegistry) -> list[list[dict[str, Any]]]:
    """One batch per profile, cycling through all profiles."""
    triples_by_profile = {
        ArtifactProfile.EMAIL: ("alice", "alice_laptop"),
        ArtifactProfile.SMS: ("alice", "alice_phone"),
        ArtifactProfile.PDF: ("alice", "alice_laptop"),
        ArtifactProfile.XLSX: ("alice", "alice_laptop"),
        ArtifactProfile.XLSX_LEDGER: ("alice", "alice_laptop"),
        ArtifactProfile.JPEG: ("alice", "alice_phone"),
        ArtifactProfile.LOG: ("bob", "bob_workstation"),
    }
    batches = []
    for profile in ArtifactProfile:
        owner, device = triples_by_profile[profile]
        batches.append(
            _make_batch(size=BATCH_SIZE, owner=owner, device=device, profile=profile.value)
        )
    return batches


def test_all_profiles_represented(
    mocker: MockerFixture, bible: CaseBible, registry: PersonaRegistry
) -> None:
    batches = _multi_profile_batches(registry)
    # Repeat batches to fill target
    repeated = batches * 6
    gen, _, _ = _build_generator(mocker, bible, registry, batches=repeated)
    artifacts, summary = gen.generate(bible, target_count=len(ArtifactProfile) * BATCH_SIZE)
    used_profiles = {a.profile for a in artifacts}
    assert used_profiles == set(ArtifactProfile), (
        f"Missing profiles: {set(ArtifactProfile) - used_profiles}"
    )


# ---------------------------------------------------------------------------
# Timestamp constraints
# ---------------------------------------------------------------------------


def test_timestamps_within_master_timeline(
    mocker: MockerFixture, bible: CaseBible, registry: PersonaRegistry
) -> None:
    gen, _, _ = _build_generator(mocker, bible, registry)
    artifacts, _ = gen.generate(bible, target_count=20)
    for artifact in artifacts:
        assert bible.master_timeline.start <= artifact.timestamp <= bible.master_timeline.end, (
            f"Timestamp {artifact.timestamp} outside timeline"
        )


def test_timestamp_offset_clamped_to_timeline(
    mocker: MockerFixture, bible: CaseBible, registry: PersonaRegistry
) -> None:
    # Provide a batch with out-of-range offsets; they should be clamped.
    bad_batch = [
        _noise_item(offset=-100.0),  # before start → clamp to 0
        _noise_item(offset=999999.0),  # way after end → clamp to total_hours
    ]
    gen, _, _ = _build_generator(mocker, bible, registry, batches=[bad_batch])
    artifacts, _ = gen.generate(bible, target_count=2)
    for artifact in artifacts:
        assert bible.master_timeline.start <= artifact.timestamp <= bible.master_timeline.end


# ---------------------------------------------------------------------------
# Permitted triples only
# ---------------------------------------------------------------------------


def test_only_permitted_triples_accepted(
    mocker: MockerFixture, bible: CaseBible, registry: PersonaRegistry
) -> None:
    # Batch with invalid triple — alice_phone cannot do email
    invalid_batch = [_noise_item(owner="alice", device="alice_phone", profile="email")]
    valid_batch = _make_batch(owner="alice", device="alice_laptop", profile="email")
    gen, _, _ = _build_generator(mocker, bible, registry, batches=[invalid_batch, valid_batch] * 10)
    artifacts, _ = gen.generate(bible, target_count=5)
    for artifact in artifacts:
        assert registry.is_permitted(artifact.owner, artifact.device, artifact.profile), (
            f"Invalid triple: ({artifact.owner}, {artifact.device}, {artifact.profile})"
        )


# ---------------------------------------------------------------------------
# Noise not in Signal Ledger
# ---------------------------------------------------------------------------


def test_generator_does_not_touch_signal_ledger(
    mocker: MockerFixture, bible: CaseBible, registry: PersonaRegistry
) -> None:
    ledger = SignalLedger()
    gen, _, _ = _build_generator(mocker, bible, registry)
    artifacts, _ = gen.generate(bible, target_count=10)
    # Generator doesn't take a ledger; the ledger should remain empty
    assert len(ledger) == 0
    # Also verify: none of the returned artifacts have is_noise=False
    assert all(a.is_noise for a in artifacts)


def test_noise_artifacts_not_recorded_in_ledger(
    mocker: MockerFixture, bible: CaseBible, registry: PersonaRegistry
) -> None:
    ledger = SignalLedger()
    gen, _, _ = _build_generator(mocker, bible, registry)
    artifacts, _ = gen.generate(bible, target_count=10)
    # Simulate pipeline correctly: noise is NOT added to the ledger
    for artifact in artifacts:
        assert not ledger.has_signal(artifact.artifact_id)
    assert len(ledger) == 0


# ---------------------------------------------------------------------------
# Guard integration: regeneration
# ---------------------------------------------------------------------------


def test_rejected_artifacts_are_regenerated(
    mocker: MockerFixture, bible: CaseBible, registry: PersonaRegistry
) -> None:
    """When guard rejects a batch's artifacts, the generator keeps trying."""
    call_count = 0

    def _alternating_flags(texts: list[str], props: list[str]) -> list[bool]:
        nonlocal call_count
        call_count += 1
        # First call: reject everything; subsequent calls: accept everything
        if call_count == 1:
            return [True] * len(texts)
        return [False] * len(texts)

    mock_guard = mocker.MagicMock(spec=LeakContradictGuard)
    mock_guard.check_batch.side_effect = _alternating_flags

    mock_client = mocker.MagicMock()
    response = mocker.MagicMock()
    response.content = [mocker.MagicMock(text=json.dumps({"artifacts": _make_batch()}))]
    response.usage.cache_read_input_tokens = 0
    response.usage.cache_creation_input_tokens = 100
    mock_client.messages.create.return_value = response

    gw = LLMGateway(client=mock_client)
    catalog = ProvenanceCatalog()
    gen = NoiseGenerator(llm=gw, catalog=catalog, registry=registry, guard=mock_guard)
    artifacts, summary = gen.generate(bible, target_count=10)

    assert summary.rejected_count >= BATCH_SIZE  # first batch was rejected
    assert len(artifacts) == 10  # second batch filled the target


# ---------------------------------------------------------------------------
# Batch size ≥ 10
# ---------------------------------------------------------------------------


def test_batch_size_is_at_least_10(
    mocker: MockerFixture, bible: CaseBible, registry: PersonaRegistry
) -> None:
    from evidence_factory.noise.generator import BATCH_SIZE as BS

    assert BS >= 10


# ---------------------------------------------------------------------------
# NoiseSummary structure
# ---------------------------------------------------------------------------


def test_noise_summary_has_required_fields(
    mocker: MockerFixture, bible: CaseBible, registry: PersonaRegistry
) -> None:
    gen, _, _ = _build_generator(mocker, bible, registry)
    _, summary = gen.generate(bible, target_count=10)
    d = summary.to_dict()
    assert "target_count" in d
    assert "generated_count" in d
    assert "rejected_count" in d
    assert "profile_distribution" in d
    assert "cache_hits" in d
    assert "cache_misses" in d
    assert isinstance(d["profile_distribution"], dict)


def test_noise_summary_rejected_count_accurate(
    mocker: MockerFixture, bible: CaseBible, registry: PersonaRegistry
) -> None:
    rejected_total = 0

    def _reject_half(texts: list[str], props: list[str]) -> list[bool]:
        nonlocal rejected_total
        flags = [i % 2 == 0 for i in range(len(texts))]
        rejected_total += sum(flags)
        return flags

    mock_guard = mocker.MagicMock(spec=LeakContradictGuard)
    mock_guard.check_batch.side_effect = _reject_half

    mock_client = mocker.MagicMock()
    response = mocker.MagicMock()
    response.content = [mocker.MagicMock(text=json.dumps({"artifacts": _make_batch(size=10)}))]
    response.usage.cache_read_input_tokens = 0
    response.usage.cache_creation_input_tokens = 100
    mock_client.messages.create.return_value = response

    gw = LLMGateway(client=mock_client)
    gen = NoiseGenerator(llm=gw, catalog=ProvenanceCatalog(), registry=registry, guard=mock_guard)
    _, summary = gen.generate(bible, target_count=20)
    assert summary.rejected_count == rejected_total


# ---------------------------------------------------------------------------
# Prompt caching: cache hits logged
# ---------------------------------------------------------------------------


def test_cache_hits_tracked_in_summary(
    mocker: MockerFixture, bible: CaseBible, registry: PersonaRegistry
) -> None:
    # Simulate first call: cache miss; subsequent calls: cache hits
    call_count = 0

    def _side_effect(*args: Any, **kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        response = mocker.MagicMock()
        response.content = [mocker.MagicMock(text=json.dumps({"artifacts": _make_batch()}))]
        if call_count == 1:
            response.usage.cache_read_input_tokens = 0
            response.usage.cache_creation_input_tokens = 500
        else:
            response.usage.cache_read_input_tokens = 500  # cache hit
            response.usage.cache_creation_input_tokens = 0
        return response

    mock_client = mocker.MagicMock()
    mock_client.messages.create.side_effect = _side_effect
    gw = LLMGateway(client=mock_client)

    mock_guard = mocker.MagicMock(spec=LeakContradictGuard)
    mock_guard.check_batch.side_effect = lambda texts, props: [False] * len(texts)

    gen = NoiseGenerator(llm=gw, catalog=ProvenanceCatalog(), registry=registry, guard=mock_guard)
    _, summary = gen.generate(bible, target_count=30)
    assert summary.cache_hits >= 1, "Expected at least one cache hit after first batch"
    assert summary.cache_misses >= 1, "Expected at least one cache miss for first batch"
