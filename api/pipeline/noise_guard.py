"""Leak/contradict guard for haystack noise.

Two phases:
  1. A pure-Python keyword/structural pre-filter that flags any noise text
     sharing distinctive terms (or structural values: amounts, dates, codes)
     with the case's true propositions.
  2. A Claude Haiku residual pass (via the LLM Gateway) on whatever survives
     the pre-filter, to catch semantic leaks with no keyword overlap.

``check_batch`` returns one bool per text — True means REJECT (drop the noise).
The pre-filter is the spine: it must reject ≥ 95% of synthetic positives and
pass innocuous noise (see tests/unit/test_noise_guard.py).
"""

from __future__ import annotations

import logging
import re

from .llm_gateway import LLMGateway

LOG = logging.getLogger("evidence_factory.noise.guard")

_STOP_WORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "is",
        "was",
        "are",
        "were",
        "be",
        "been",
        "has",
        "have",
        "had",
        "do",
        "did",
        "does",
        "not",
        "no",
        "nor",
        "so",
        "yet",
        "both",
        "either",
        "that",
        "this",
        "these",
        "those",
        "which",
        "who",
        "whom",
        "whose",
        "what",
        "where",
        "when",
        "why",
        "how",
        "all",
        "any",
        "each",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "than",
        "too",
        "very",
        "just",
        "as",
        "its",
        "it",
        "he",
        "she",
        "we",
        "they",
        "their",
        "our",
        "your",
        "my",
        "his",
        "her",
        "i",
        "am",
        "into",
        "about",
        "up",
        "out",
        "if",
        "then",
        "also",
        "after",
        "before",
    }
)

_STRUCTURAL_PATTERNS = [
    r"\$[\d,]+",  # dollar amounts e.g. $50,000
    r"\b\d{4}-\d{2}-\d{2}\b",  # ISO dates
    r"\b\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)\b",  # times
    r"\b[A-Z]{2,}-\d{4,}\b",  # account/reference codes
]
_STRUCTURAL_RE = [re.compile(p) for p in _STRUCTURAL_PATTERNS]


def _extract_key_terms(propositions: list[str]) -> set[str]:
    """Lower-cased distinctive tokens + structural values from all propositions."""
    terms: set[str] = set()
    for prop in propositions:
        for word in re.findall(r"\b[a-zA-Z]{4,}\b", prop):
            w = word.lower()
            if w not in _STOP_WORDS:
                terms.add(w)
        for pattern in _STRUCTURAL_RE:
            for match in pattern.finditer(prop):
                terms.add(match.group().lower())
    return terms


def _count_term_hits(text: str, terms: set[str]) -> int:
    text_lower = text.lower()
    return sum(1 for t in terms if t in text_lower)


class LeakContradictGuard:
    """Keyword/structural pre-filter → Haiku residual pass."""

    # Flagged by the pre-filter when an artifact matches this many distinct
    # key terms from the true propositions.
    PREFILTER_THRESHOLD = 2

    def __init__(self, llm: LLMGateway) -> None:
        self._llm = llm

    def check_batch(
        self,
        artifact_texts: list[str],
        true_propositions: list[str],
    ) -> list[bool]:
        """Phase-1 pre-filter, then Phase-2 LLM check on residuals.
        Returns one bool per text — True means REJECT."""
        if not artifact_texts:
            return []

        phase1 = self.keyword_prefilter(artifact_texts, true_propositions)
        LOG.debug("noise guard pre-filter flagged %d/%d", sum(phase1), len(phase1))

        residual_indices = [i for i, flagged in enumerate(phase1) if not flagged]
        residual_texts = [artifact_texts[i] for i in residual_indices]
        if not residual_texts:
            return phase1

        llm_flags = self._llm.guard_check_batch(residual_texts, true_propositions)
        combined = list(phase1)
        for offset, orig_idx in enumerate(residual_indices):
            if offset < len(llm_flags):
                combined[orig_idx] = combined[orig_idx] or llm_flags[offset]
        return combined

    def keyword_prefilter(
        self,
        artifact_texts: list[str],
        true_propositions: list[str],
    ) -> list[bool]:
        """Flag a text if it contains ≥ PREFILTER_THRESHOLD distinct key terms."""
        key_terms = _extract_key_terms(true_propositions)
        if not key_terms:
            return [False] * len(artifact_texts)
        return [
            _count_term_hits(text, key_terms) >= self.PREFILTER_THRESHOLD for text in artifact_texts
        ]
