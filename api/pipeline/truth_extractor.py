"""Truth Extractor — skeleton.

SourceText → CanonicalTruth (prose outline + proposition graph).

Slice 1 keeps this trivial: split the source into sentences, take the first
3-5 substantive sentences as propositions, and let the LLM Gateway emit the
prose outline. Later slices replace the rule-based extractor with a real
Sonnet/Opus call; the interface stays the same.
"""

from __future__ import annotations

import re

from .llm_gateway import LLMGateway
from .source_intake import SourceText
from .types import CanonicalTruth, Proposition, PropositionGraph

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def extract_truth(
    source: SourceText,
    *,
    gateway: LLMGateway,
    max_propositions: int = 4,
) -> CanonicalTruth:
    sentences = [s.strip() for s in _SENT_SPLIT.split(source.body) if len(s.strip()) > 20]
    if not sentences:
        raise ValueError("source produced no substantive sentences")

    selected = sentences[:max_propositions]
    propositions = tuple(
        Proposition(id=f"prop_{i + 1}", text=text) for i, text in enumerate(selected)
    )

    # Note: cache_key intentionally derived from source content so a
    # repeated extraction inside one run hits the prompt cache.
    outline = gateway.complete(
        "truth_extractor",
        f"Summarise the following source into a canonical truth outline:\n\n{source.body[:2000]}",
        cache_key=f"source::{source.char_count}",
    )

    return CanonicalTruth(outline=outline, graph=PropositionGraph(propositions=propositions))
