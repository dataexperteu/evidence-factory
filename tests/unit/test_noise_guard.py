"""Leak/contradict guard tests.

The pure-Python keyword pre-filter is the spine characterization test: it must
reject >= 95% of synthetic positive fixtures and pass innocuous noise. The LLM
residual pass is exercised in fixture mode (it passes residuals, so check_batch
reduces to the pre-filter)."""

from __future__ import annotations

import pytest

from api.pipeline.llm_gateway import LLMGateway
from api.pipeline.noise_guard import LeakContradictGuard, _extract_key_terms

TRUE_PROPOSITIONS = [
    "Alice transferred $50,000 to the Cayman Islands account on January 15, 2024",
    "Bob deleted the audit logs at 3:47 AM on January 16, 2024",
    "Charlie created a fake invoice for $25,000 to Meridian LLC",
]


@pytest.fixture()
def guard() -> LeakContradictGuard:
    return LeakContradictGuard(LLMGateway(mode="fixture"))


def test_extract_key_terms_basic() -> None:
    terms = _extract_key_terms(TRUE_PROPOSITIONS)
    assert "transferred" in terms
    assert "deleted" in terms
    assert "invoice" in terms
    assert "the" not in terms
    assert "and" not in terms


def test_extract_key_terms_dollar_amounts() -> None:
    terms = _extract_key_terms(TRUE_PROPOSITIONS)
    assert "$50,000" in terms or any("50,000" in t for t in terms)


def test_extract_key_terms_empty() -> None:
    assert _extract_key_terms([]) == set()


def test_prefilter_rejects_proposition_text(guard: LeakContradictGuard) -> None:
    artifacts = [
        "FYI — Alice transferred $50,000 to the Cayman Islands account yesterday.",
        "The audit logs were deleted by Bob at 3:47 in the morning.",
    ]
    flags = guard.keyword_prefilter(artifacts, TRUE_PROPOSITIONS)
    assert flags[0] is True
    assert flags[1] is True


def test_prefilter_passes_innocuous_noise(guard: LeakContradictGuard) -> None:
    innocuous = [
        "Can you grab lunch on Thursday?",
        "Please review the attached slide deck before the meeting.",
        "Happy birthday! Hope you have a great day.",
        "Reminder: office closes early on Friday.",
    ]
    flags = guard.keyword_prefilter(innocuous, TRUE_PROPOSITIONS)
    assert all(f is False for f in flags), f"unexpected flags: {flags}"


def test_prefilter_empty_propositions(guard: LeakContradictGuard) -> None:
    assert guard.keyword_prefilter(["some text"], []) == [False]


def test_prefilter_empty_artifacts(guard: LeakContradictGuard) -> None:
    assert guard.keyword_prefilter([], TRUE_PROPOSITIONS) == []


def test_check_batch_empty(guard: LeakContradictGuard) -> None:
    assert guard.check_batch([], TRUE_PROPOSITIONS) == []


def test_check_batch_combines_prefilter(guard: LeakContradictGuard) -> None:
    texts = [
        "Alice transferred $50,000 to the Cayman Islands offshore account.",
        "Good morning, please send the report by EOD.",
    ]
    flags = guard.check_batch(texts, TRUE_PROPOSITIONS)
    assert flags[0] is True  # caught by pre-filter
    assert flags[1] is False  # innocuous, fixture LLM passes residual


def test_keyword_prefilter_95_pct_rejection_rate(guard: LeakContradictGuard) -> None:
    """Spine characterization: pre-filter rejects >= 95% of synthetic positives."""
    synthetic_positives = [
        "Alice transferred $50,000 overseas.",
        "The CFO transferred $50,000 to Cayman.",
        "Alice moved $50,000 offshore on January 15.",
        "Wire transfer of $50,000 authorised by Alice.",
        "Alice's transfer of $50,000 to offshore account confirmed.",
        "There was a $50,000 transfer linked to Alice.",
        "Alice transferred funds ($50,000) abroad.",
        "Bob deleted the audit logs at night.",
        "The audit logs were deleted by Bob.",
        "Bob removed audit logs on January 16.",
        "Audit logs deletion traced to Bob, 3:47 AM.",
        "Logs deleted — action attributed to Bob.",
        "Bob's activity: audit log deletion at 3 AM.",
        "Charlie created a $25,000 invoice for Meridian.",
        "A fake invoice ($25,000) was created by Charlie.",
        "Charlie submitted a fraudulent $25,000 invoice.",
        "Charlie's invoice to Meridian LLC totalled $25,000.",
        "Bob deleted logs; Alice transferred $50,000.",
        "Invoice of $25,000 created; audit logs deleted.",
        "Alice transferred $50,000; Bob deleted evidence.",
    ]
    assert len(synthetic_positives) == 20
    flags = guard.keyword_prefilter(synthetic_positives, TRUE_PROPOSITIONS)
    rejection_rate = sum(flags) / len(flags)
    assert rejection_rate >= 0.95, (
        f"pre-filter rejection rate {rejection_rate:.0%} < 95% (flagged {sum(flags)}/{len(flags)})"
    )


def test_prefilter_no_false_positives_on_innocuous(guard: LeakContradictGuard) -> None:
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
    flags = guard.keyword_prefilter(innocuous, TRUE_PROPOSITIONS)
    assert sum(flags) == 0, f"false positives: {[innocuous[i] for i, f in enumerate(flags) if f]}"
