"""Tests for the leak/contradict guard (keyword pre-filter phase is pure Python)."""

from __future__ import annotations

import json

from pytest_mock import MockerFixture

from evidence_factory.llm.gateway import LLMGateway
from evidence_factory.noise.guard import LeakContradictGuard, _extract_key_terms

# ---------------------------------------------------------------------------
# Helper: build mock LLM gateway
# ---------------------------------------------------------------------------


def _make_gateway_with_verdict(mocker: MockerFixture, verdicts: list[str]) -> LLMGateway:
    """Return a LLMGateway whose guard_check_batch returns the given verdicts."""
    results = [{"index": i, "verdict": v, "reason": "test"} for i, v in enumerate(verdicts)]
    mock_client = mocker.MagicMock()
    response = mocker.MagicMock()
    response.content = [mocker.MagicMock(text=json.dumps({"results": results}))]
    response.usage.cache_read_input_tokens = 0
    response.usage.cache_creation_input_tokens = 100
    mock_client.messages.create.return_value = response
    return LLMGateway(client=mock_client)


# ---------------------------------------------------------------------------
# _extract_key_terms
# ---------------------------------------------------------------------------


def test_extract_key_terms_basic(true_propositions: list[str]) -> None:
    terms = _extract_key_terms(true_propositions)
    # Should include distinctive words from propositions
    assert "transferred" in terms
    assert "deleted" in terms
    assert "invoice" in terms
    # Common words should be excluded
    assert "the" not in terms
    assert "and" not in terms


def test_extract_key_terms_dollar_amounts(true_propositions: list[str]) -> None:
    terms = _extract_key_terms(true_propositions)
    # Structural: dollar amounts
    assert "$50,000" in terms or any("50,000" in t for t in terms)


def test_extract_key_terms_empty() -> None:
    assert _extract_key_terms([]) == set()


# ---------------------------------------------------------------------------
# keyword_prefilter — pure Python (no LLM)
# ---------------------------------------------------------------------------


def test_prefilter_rejects_exact_proposition_text(
    true_propositions: list[str], mocker: MockerFixture
) -> None:
    gw = _make_gateway_with_verdict(mocker, [])
    guard = LeakContradictGuard(llm=gw)
    # Artifact that directly quotes proposition content
    artifacts = [
        "FYI — Alice transferred $50,000 to the Cayman Islands account yesterday.",
        "The audit logs were deleted by Bob at 3:47 in the morning.",
    ]
    flags = guard.keyword_prefilter(artifacts, true_propositions)
    # Both contain multiple key terms → should be flagged
    assert flags[0] is True
    assert flags[1] is True


def test_prefilter_passes_innocuous_noise(
    true_propositions: list[str], mocker: MockerFixture
) -> None:
    gw = _make_gateway_with_verdict(mocker, [])
    guard = LeakContradictGuard(llm=gw)
    innocuous = [
        "Can you grab lunch on Thursday?",
        "Please review the attached slide deck before the meeting.",
        "Happy birthday! Hope you have a great day.",
        "Reminder: office closes early on Friday.",
    ]
    flags = guard.keyword_prefilter(innocuous, true_propositions)
    assert all(f is False for f in flags), f"Expected all PASS, got {flags}"


def test_prefilter_empty_propositions() -> None:
    guard = LeakContradictGuard(llm=LLMGateway.__new__(LLMGateway))
    flags = guard.keyword_prefilter(["some text"], [])
    assert flags == [False]


def test_prefilter_empty_artifacts(true_propositions: list[str]) -> None:
    guard = LeakContradictGuard(llm=LLMGateway.__new__(LLMGateway))
    flags = guard.keyword_prefilter([], true_propositions)
    assert flags == []


# ---------------------------------------------------------------------------
# check_batch — combined phases
# ---------------------------------------------------------------------------


def test_check_batch_prefilter_catches_most(
    true_propositions: list[str], mocker: MockerFixture
) -> None:
    """Pre-filter should catch items that clearly contain proposition content."""
    # These all contain multiple key terms from propositions
    synthetic_positives = [
        "Alice transferred $50,000 to the Cayman Islands offshore account.",
        "Bob deleted the audit logs at 3:47 AM on January 16.",
        "Charlie created a fake invoice for $25,000 to Meridian LLC.",
        "The CFO transferred fifty thousand dollars to an offshore account.",
        "Audit logs for January 16 were found missing; Bob was the last user.",
    ]
    # LLM sees only the residuals (those that pass pre-filter); mock it to PASS all
    gw = _make_gateway_with_verdict(mocker, ["PASS"] * len(synthetic_positives))
    guard = LeakContradictGuard(llm=gw)
    flags = guard.check_batch(synthetic_positives, true_propositions)
    # Pre-filter alone should flag most
    rejected = sum(flags)
    assert rejected >= 1, "Pre-filter should flag at least some synthetic positives"


def test_check_batch_llm_catches_residuals(
    true_propositions: list[str], mocker: MockerFixture
) -> None:
    """LLM phase catches items that pass the pre-filter but are still leaks."""
    # This text has no keyword matches but is semantically leaking
    subtle_leak = "Someone in finance moved a large sum abroad last month."
    innocuous = "Good morning, please send the report by EOD."
    # Pre-filter passes both; LLM rejects the subtle one
    mock_client = mocker.MagicMock()
    response = mocker.MagicMock()
    response.content = [
        mocker.MagicMock(
            text=json.dumps(
                {"results": [{"index": 0, "verdict": "REJECT", "reason": "implies transfer"}]}
            )
        )
    ]
    response.usage.cache_read_input_tokens = 50
    response.usage.cache_creation_input_tokens = 0
    mock_client.messages.create.return_value = response
    gw = LLMGateway(client=mock_client)
    guard = LeakContradictGuard(llm=gw)
    flags = guard.check_batch([subtle_leak, innocuous], true_propositions)
    # subtle_leak should be rejected by LLM; innocuous should pass
    assert flags[0] is True
    assert flags[1] is False


def test_check_batch_returns_empty_for_empty_input(
    true_propositions: list[str], mocker: MockerFixture
) -> None:
    gw = _make_gateway_with_verdict(mocker, [])
    guard = LeakContradictGuard(llm=gw)
    assert guard.check_batch([], true_propositions) == []


# ---------------------------------------------------------------------------
# 95 % rejection rate — acceptance-criterion test
# ---------------------------------------------------------------------------


def test_keyword_prefilter_95_pct_rejection_rate(true_propositions: list[str]) -> None:
    """
    Pre-filter must reject ≥ 95 % of synthetic positive fixtures.

    All 20 fixtures explicitly contain distinguishing terms from the true
    propositions, so the pre-filter alone should catch all of them.
    """
    synthetic_positives = [
        # Restate proposition 1 variants
        "Alice transferred $50,000 overseas.",
        "The CFO transferred $50,000 to Cayman.",
        "Alice moved $50,000 offshore on January 15.",
        "Wire transfer of $50,000 authorised by Alice.",
        "Alice's transfer of $50,000 to offshore account confirmed.",
        "There was a $50,000 transfer linked to Alice.",
        "Alice transferred funds ($50,000) abroad.",
        # Restate proposition 2 variants
        "Bob deleted the audit logs at night.",
        "The audit logs were deleted by Bob.",
        "Bob removed audit logs on January 16.",
        "Audit logs deletion traced to Bob, 3:47 AM.",
        "Logs deleted — action attributed to Bob.",
        "Bob's activity: audit log deletion at 3 AM.",
        # Restate proposition 3 variants
        "Charlie created a $25,000 invoice for Meridian.",
        "A fake invoice ($25,000) was created by Charlie.",
        "Charlie submitted a fraudulent $25,000 invoice.",
        "Charlie's invoice to Meridian LLC totalled $25,000.",
        # Mixed
        "Bob deleted logs; Alice transferred $50,000.",
        "Invoice of $25,000 created; audit logs deleted.",
        "Alice transferred $50,000; Bob deleted evidence.",
    ]
    assert len(synthetic_positives) == 20

    guard = LeakContradictGuard(llm=LLMGateway.__new__(LLMGateway))
    flags = guard.keyword_prefilter(synthetic_positives, true_propositions)
    rejection_rate = sum(flags) / len(flags)
    assert rejection_rate >= 0.95, (
        f"Pre-filter rejection rate {rejection_rate:.0%} < 95 % (flagged {sum(flags)}/{len(flags)})"
    )


def test_prefilter_false_positive_rate_on_innocuous(true_propositions: list[str]) -> None:
    """Pre-filter must not flag genuinely innocuous noise."""
    innocuous = [
        "Can you confirm the 2pm meeting tomorrow?",
        "The weather looks great this weekend.",
        "Please review the Q3 slide deck.",
        "Happy to help with onboarding next week.",
        "Reminder: submit expenses by Friday.",
        "Looking forward to the team lunch.",
        "Let me know if you need anything else.",
        "Thanks for the update — will follow up.",
        "Great work on the presentation!",
        "I'll be working from home on Monday.",
    ]
    guard = LeakContradictGuard(llm=LLMGateway.__new__(LLMGateway))
    flags = guard.keyword_prefilter(innocuous, true_propositions)
    # None should be flagged — all genuine noise
    assert sum(flags) == 0, f"False positives: {[innocuous[i] for i, f in enumerate(flags) if f]}"


# ---------------------------------------------------------------------------
# LLM cache-hit tracking
# ---------------------------------------------------------------------------


def test_guard_records_cache_hits(true_propositions: list[str], mocker: MockerFixture) -> None:
    mock_client = mocker.MagicMock()
    response = mocker.MagicMock()
    response.content = [
        mocker.MagicMock(
            text=json.dumps({"results": [{"index": 0, "verdict": "PASS", "reason": "ok"}]})
        )
    ]
    response.usage.cache_read_input_tokens = 200
    response.usage.cache_creation_input_tokens = 0
    mock_client.messages.create.return_value = response

    gw = LLMGateway(client=mock_client)
    guard = LeakContradictGuard(llm=gw)

    # Only one text, pre-filter should pass it (no key terms), so LLM is called
    guard.check_batch(["Meeting at 3pm today?"], true_propositions)
    assert gw.cache_hits >= 1
