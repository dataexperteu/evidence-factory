from __future__ import annotations

import logging
import re

from ..llm.gateway import LLMGateway

logger = logging.getLogger(__name__)

# Words too common to count as "key terms" even if they appear in propositions.
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

# Regex patterns for "structural" signals that reveal case facts.
_STRUCTURAL_PATTERNS = [
    r"\$[\d,]+",  # dollar amounts e.g. $50,000
    r"\b\d{4}-\d{2}-\d{2}\b",  # ISO dates
    r"\b\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)\b",  # times
    r"\b[A-Z]{2,}-\d{4,}\b",  # account/reference codes
]
_STRUCTURAL_RE = [re.compile(p) for p in _STRUCTURAL_PATTERNS]


def _extract_key_terms(propositions: list[str]) -> set[str]:
    """Return lower-cased tokens and structural values from all propositions."""
    terms: set[str] = set()
    for prop in propositions:
        # Word tokens (≥4 chars, not stop words)
        for word in re.findall(r"\b[a-zA-Z]{4,}\b", prop):
            w = word.lower()
            if w not in _STOP_WORDS:
                terms.add(w)
        # Structural values (amounts, dates, codes)
        for pattern in _STRUCTURAL_RE:
            for match in pattern.finditer(prop):
                terms.add(match.group().lower())
    return terms


def _count_term_hits(text: str, terms: set[str]) -> int:
    """Count how many key terms appear in text."""
    text_lower = text.lower()
    return sum(1 for t in terms if t in text_lower)


class LeakContradictGuard:
    """
    Two-phase guard:
      1. Keyword/structural pre-filter (pure Python, no LLM).
      2. Haiku LLM pass on any residual candidates that pass the pre-filter.

    Returns True for each artifact that should be REJECTED.
    """

    # An artifact is flagged by the pre-filter if it matches this many
    # distinct key terms from the true propositions.
    PREFILTER_THRESHOLD = 2

    def __init__(self, llm: LLMGateway) -> None:
        self._llm = llm

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_batch(
        self,
        artifact_texts: list[str],
        true_propositions: list[str],
    ) -> list[bool]:
        """
        Phase-1 pre-filter, then Phase-2 LLM check on residuals.
        Returns list[bool] — True means REJECT.
        """
        if not artifact_texts:
            return []

        phase1_flags = self.keyword_prefilter(artifact_texts, true_propositions)
        logger.debug("Pre-filter: %d/%d flagged", sum(phase1_flags), len(phase1_flags))

        # Only send non-flagged items to the LLM.
        residual_indices = [i for i, f in enumerate(phase1_flags) if not f]
        residual_texts = [artifact_texts[i] for i in residual_indices]

        if not residual_texts:
            return phase1_flags

        llm_flags = self._llm.guard_check_batch(residual_texts, true_propositions)

        # Merge results back.
        combined = list(phase1_flags)
        for offset, orig_idx in enumerate(residual_indices):
            if offset < len(llm_flags):
                combined[orig_idx] = combined[orig_idx] or llm_flags[offset]

        llm_rejected = sum(llm_flags)
        if llm_rejected:
            logger.info("LLM guard rejected %d additional artifacts", llm_rejected)

        return combined

    def keyword_prefilter(
        self,
        artifact_texts: list[str],
        true_propositions: list[str],
    ) -> list[bool]:
        """
        Pure-Python pre-filter. Flags an artifact if it contains
        ≥ PREFILTER_THRESHOLD distinct key terms from the true propositions.
        """
        key_terms = _extract_key_terms(true_propositions)
        if not key_terms:
            return [False] * len(artifact_texts)

        flags: list[bool] = []
        for text in artifact_texts:
            hits = _count_term_hits(text, key_terms)
            flags.append(hits >= self.PREFILTER_THRESHOLD)

        return flags
