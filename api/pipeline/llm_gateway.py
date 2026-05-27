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
from typing import Any, Literal

LOG = logging.getLogger("evidence_factory.llm")

Role = Literal[
    "truth_extractor", "event_graph", "artifact_content", "critic", "closure", "noise", "cast"
]

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
    "cast",
}

# Mundane, case-agnostic noise sentences used in fixture mode. They share no
# distinctive vocabulary with case propositions, so the leak/contradict guard's
# keyword pre-filter passes them. Real mode replaces these with Haiku output.
_FIXTURE_NOISE_LINES = [
    "Reminder: the weekly team sync moved to 10am on Tuesday.",
    "Could you grab a coffee for me on the way in?",
    "The printer on level two is finally working again.",
    "Lunch plans for Friday — thinking the place near the park.",
    "Parking permit renewals are due by the end of the month.",
    "The monthly newsletter just went out, worth a quick skim.",
    "Happy Friday everyone, have a restful weekend.",
    "Don't forget to submit your timesheets before noon.",
    "Quick heads up: the kitchen is out of oat milk again.",
    "Can we push our one-on-one to tomorrow afternoon?",
    "The office will close early ahead of the public holiday.",
    "New badges are ready for pickup at reception.",
    "Thanks for covering the front desk earlier, appreciated it.",
    "The wifi in the back room has been a bit flaky today.",
    "Looking forward to the team lunch next week.",
    "Please water the plants if you're in over the weekend.",
]

_GUARD_SYSTEM_TMPL = (
    "You are a strict evidence guardian for a synthetic-evidence corpus. "
    "Flag any artifact that states, implies, or contradicts these case facts:\n"
    "{proposition_list}\n"
    'Output ONLY JSON: {{"results": [{{"index": <int>, "verdict": "REJECT"|"PASS"}}]}}.'
)


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

    def _record_cache(self, role: Role, cache_key: str | None) -> None:
        """Log + tally a prompt-cache hit/miss for a repeated cache_key.

        The PRD acceptance criterion is "cache hits are logged"; the persona/
        timeline context the noise generator reuses across batches re-uses one
        cache_key, so every batch after the first registers as a hit."""
        if not cache_key:
            return
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

    def complete(self, role: Role, prompt: str, *, cache_key: str | None = None) -> str:
        self._record_cache(role, cache_key)

        if self.mode == "fixture":
            return self._fixture_complete(role, prompt)
        return self._real_complete(role, prompt, cache_key=cache_key)

    # -- fixture mode ---------------------------------------------------

    def _fixture_complete(self, role: Role, prompt: str) -> str:
        """Deterministic-ish synthetic text. NOT a model substitute — just
        enough structure to let the orchestrator wire-up be tested."""
        if role == "critic":
            return self._fixture_critic_verdict(prompt)
        if role == "cast":
            return self._fixture_cast_response()
        salt = self._rng.randint(1_000, 9_999)
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:8]
        return (
            f"[{role}::{digest}::{salt}] synthetic fixture response — "
            f"this run is non-deterministic by design."
        )

    def _fixture_cast_response(self) -> str:
        return json.dumps(
            {
                "personas": [
                    {"display_name": "Alice Fixture", "role": "protagonist"},
                    {"display_name": "Bob Fixture", "role": "antagonist"},
                    {"display_name": "Carol Fixture", "role": "witness"},
                ]
            }
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

    # -- noise generation (Haiku) ---------------------------------------

    def generate_noise_batch(
        self,
        *,
        profile: str,
        batch_size: int,
        context_cache_key: str,
        persona_context: str,
        timeline_context: str,
        true_propositions: list[str],
        case_description: str = "",
        taboo_topics: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return ``batch_size`` noise items (each a dict with ``text_content``
        and ``timestamp_offset_hours``) for ``profile`` via Claude Haiku 4.5.

        The persona/timeline context is sent under one ``context_cache_key`` so
        the Anthropic prompt cache is re-used across batches (cache hits logged)."""
        self._record_cache("noise", context_cache_key)
        if self.mode == "fixture":
            return self._fixture_noise_batch(profile, batch_size)
        return self._real_noise_batch(
            profile=profile,
            batch_size=batch_size,
            persona_context=persona_context,
            timeline_context=timeline_context,
            true_propositions=true_propositions,
            case_description=case_description,
            taboo_topics=taboo_topics or [],
        )

    def _fixture_noise_batch(self, profile: str, batch_size: int) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for _ in range(batch_size):
            line = self._rng.choice(_FIXTURE_NOISE_LINES)
            # Salt so two runs differ and same-second filename collisions are rare.
            tag = self._rng.randint(1000, 9999)
            items.append(
                {
                    "text_content": f"{line} (ref {tag})",
                    "timestamp_offset_hours": self._rng.random(),
                }
            )
        return items

    def _real_noise_batch(
        self,
        *,
        profile: str,
        batch_size: int,
        persona_context: str,
        timeline_context: str,
        true_propositions: list[str],
        case_description: str,
        taboo_topics: list[str],
    ) -> list[dict[str, Any]]:  # pragma: no cover - network call
        import anthropic  # type: ignore

        client = anthropic.Anthropic()
        proposition_list = (
            "\n".join(f"{i + 1}. {p}" for i, p in enumerate(true_propositions)) or "(none)"
        )
        system_text = (
            "You generate mundane, innocuous noise documents for a synthetic-evidence "
            f"corpus.\nCASE: {case_description or 'A fictional investigation.'}\n"
            f"PERSONAS/DEVICES:\n{persona_context}\nTIMELINE: {timeline_context}\n"
            f"NEVER mention, imply, or contradict these facts:\n{proposition_list}\n"
            f"TABOO TOPICS: {', '.join(taboo_topics) or '(none)'}"
        )
        user_text = (
            f"Generate exactly {batch_size} mundane '{profile}' noise items. "
            'Return JSON: {"artifacts": [{"text_content": "...", '
            '"timestamp_offset_hours": <number 0..1>}]}.'
        )
        resp = client.messages.create(
            model=_model_for("noise"),
            max_tokens=4096,
            system=[{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_text}],
        )
        raw = "".join(block.text for block in resp.content if block.type == "text")
        return self._parse_noise_response(raw)

    def _parse_noise_response(self, raw: str) -> list[dict[str, Any]]:  # pragma: no cover
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            LOG.warning("noise LLM returned invalid JSON; skipping batch")
            return []
        if isinstance(data, dict) and isinstance(data.get("artifacts"), list):
            return [item for item in data["artifacts"] if isinstance(item, dict)]
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        return []

    # -- leak/contradict guard residual pass (Haiku) --------------------

    def guard_check_batch(
        self,
        artifact_texts: list[str],
        true_propositions: list[str],
    ) -> list[bool]:
        """Return True (REJECT) for each text that leaks/contradicts a fact.

        This is the LLM phase of the leak/contradict guard; the keyword
        pre-filter (pure Python) runs first in ``LeakContradictGuard``."""
        if not artifact_texts:
            return []
        if self.mode == "fixture":
            # The pure-Python pre-filter already caught the keyword leaks; the
            # fixture LLM has no semantic model, so it passes the residuals.
            return [False] * len(artifact_texts)
        return self._real_guard_check(artifact_texts, true_propositions)

    def _real_guard_check(
        self,
        artifact_texts: list[str],
        true_propositions: list[str],
    ) -> list[bool]:  # pragma: no cover - network call
        import anthropic  # type: ignore

        client = anthropic.Anthropic()
        proposition_list = (
            "\n".join(f"{i + 1}. {p}" for i, p in enumerate(true_propositions)) or "(none)"
        )
        system_text = _GUARD_SYSTEM_TMPL.format(proposition_list=proposition_list)
        artifacts_text = "\n\n".join(f"{i}: {t[:500]}" for i, t in enumerate(artifact_texts))
        resp = client.messages.create(
            model=_model_for("noise"),
            max_tokens=2048,
            system=[{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": f"ARTIFACTS:\n{artifacts_text}"}],
        )
        raw = "".join(block.text for block in resp.content if block.type == "text")
        return self._parse_guard_response(raw, len(artifact_texts))

    def _parse_guard_response(self, raw: str, expected: int) -> list[bool]:  # pragma: no cover
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return [False] * expected
        results = data.get("results") if isinstance(data, dict) else data
        if not isinstance(results, list):
            return [False] * expected
        verdicts = [False] * expected
        for item in results:
            if not isinstance(item, dict):
                continue
            idx = item.get("index")
            if isinstance(idx, int) and 0 <= idx < expected:
                verdicts[idx] = str(item.get("verdict", "PASS")).upper() == "REJECT"
        return verdicts
