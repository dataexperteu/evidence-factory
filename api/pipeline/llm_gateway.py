"""LLM Gateway.

This is the single integration point with the Anthropic SDK. Slice 1 ships
a *fixture-mode* gateway because the sandbox has no external network access;
the real-mode path is present so subsequent slices can flip it on without
restructuring callers.

What it gives you today:
- A `complete(role, prompt, cache_key=...)` API that returns text.
- Prompt-cache emulation: the gateway logs `cache_hit=True` whenever a call
  re-uses a `cache_key` it has seen before in the same run. The PRD's
  acceptance criterion is "cache hits are logged"; that is what we satisfy.
- Tiered roles: Sonnet/Opus for reasoning roles, Haiku for noise (the noise
  generator does not run in slice 1, but the routing is in place).

Real mode (Anthropic SDK) is wired so the slice 2 author only needs to set
`ANTHROPIC_API_KEY` and `EVIDENCE_FACTORY_LLM_MODE=real`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
from dataclasses import dataclass, field
from typing import Literal

LOG = logging.getLogger("evidence_factory.llm")

Role = Literal["truth_extractor", "event_graph", "artifact_content", "critic", "closure", "noise"]

# The smoking-gun bar: a single load-bearing artifact carrying this much (or
# more) signal weight is judged strong enough to prove its bound proposition on
# its own. The fixture critic uses it to stand in for the LLM's judgement; the
# Remediator drives every flagged artifact below this bar (split halves the
# weight per fragment, dilute scales it down), so the bounded re-critic loop
# terminates. Real-mode critique ignores this constant entirely.
SMOKING_GUN_WEIGHT_THRESHOLD = 1.0

_WEIGHT_RE = re.compile(r"signal_weight=([0-9]*\.?[0-9]+)")

_REASONING_ROLES: set[Role] = {
    "truth_extractor",
    "event_graph",
    "artifact_content",
    "critic",
    "closure",
}


def _model_for(role: Role) -> str:
    # PRD: Opus 4.7 or Sonnet 4.6 for reasoning, Haiku 4.5 for noise.
    if role in _REASONING_ROLES:
        return "claude-sonnet-4-6"
    return "claude-haiku-4-5"


@dataclass
class LLMCacheStats:
    hits: int = 0
    misses: int = 0
    cache_keys_seen: set[str] = field(default_factory=set)


class LLMGateway:
    """Single integration point for the Anthropic SDK."""

    def __init__(
        self,
        *,
        mode: str | None = None,
        rng_seed: int | None = None,
    ) -> None:
        self.mode = mode or os.environ.get("EVIDENCE_FACTORY_LLM_MODE", "fixture")
        self.stats = LLMCacheStats()
        # Per-run fresh randomness so the acceptance criterion
        # "identical inputs produce a different corpus" is satisfied.
        self._rng = random.Random(rng_seed)

    def complete(self, role: Role, prompt: str, *, cache_key: str | None = None) -> str:
        if cache_key:
            if cache_key in self.stats.cache_keys_seen:
                self.stats.hits += 1
                LOG.info(
                    "llm_cache cache_hit=True role=%s cache_key=%s model=%s",
                    role,
                    cache_key,
                    _model_for(role),
                )
            else:
                self.stats.misses += 1
                self.stats.cache_keys_seen.add(cache_key)
                LOG.info(
                    "llm_cache cache_hit=False role=%s cache_key=%s model=%s",
                    role,
                    cache_key,
                    _model_for(role),
                )

        if self.mode == "fixture":
            return self._fixture_complete(role, prompt)
        return self._real_complete(role, prompt, cache_key=cache_key)

    # -- fixture mode ---------------------------------------------------

    def _fixture_complete(self, role: Role, prompt: str) -> str:
        """Deterministic-ish synthetic text. NOT a model substitute — just
        enough structure to let the orchestrator wire-up be tested."""
        if role == "critic":
            return self._fixture_critic_verdict(prompt)
        salt = self._rng.randint(1_000, 9_999)
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:8]
        return (
            f"[{role}::{digest}::{salt}] synthetic fixture response — "
            f"this run is non-deterministic by design."
        )

    def _fixture_critic_verdict(self, prompt: str) -> str:
        """Stand in for the Smoking-Gun Critic LLM with a structured verdict.

        The prompt carries a machine-readable `signal_weight=` token; a single
        artifact at or above the smoking-gun bar is judged `too_strong`. This
        keeps the fixture deterministic (so the e2e smoke reliably triggers
        remediation) while still exercising the real JSON verdict schema."""
        match = _WEIGHT_RE.search(prompt)
        weight = float(match.group(1)) if match else 0.0
        too_strong = weight >= SMOKING_GUN_WEIGHT_THRESHOLD
        reason = (
            "single artifact at full signal weight would prove the bound proposition outright"
            if too_strong
            else "signal corroborates but is not individually dispositive"
        )
        return json.dumps({"too_strong": too_strong, "reason": reason})

    # -- real mode ------------------------------------------------------

    def _real_complete(self, role: Role, prompt: str, *, cache_key: str | None) -> str:
        # Lazy import so fixture-mode runs do not require the SDK env.
        try:
            import anthropic  # type: ignore
        except ImportError as e:  # pragma: no cover - real-mode only
            raise RuntimeError("real LLM mode requires the anthropic package") from e

        client = anthropic.Anthropic()
        system_block: dict[str, object] = {"type": "text", "text": f"role={role}"}
        if cache_key:
            # PRD: prompt caching for repeated persona/timeline context.
            system_block["cache_control"] = {"type": "ephemeral"}

        resp = client.messages.create(  # pragma: no cover - network call
            model=_model_for(role),
            max_tokens=1024,
            system=[system_block],  # type: ignore[list-item]
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in resp.content if block.type == "text")
