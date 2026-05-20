from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

_HAIKU_MODEL = "claude-haiku-4-5-20251001"

_NOISE_SYSTEM_TMPL = """\
You are a forensic document generator for the Evidence Factory system.
Your task is to generate realistic, mundane noise artifacts for a synthetic evidence corpus.

CASE BIBLE:
{case_description}

PERSONAS AND DEVICES:
{persona_context}

MASTER TIMELINE:
Start: {timeline_start}
End:   {timeline_end}
Total hours available: {total_hours:.1f}

TABOO TOPICS — never mention these in any form:
{taboo_list}

TRUE PROPOSITIONS — never restate, hint at, imply, or contradict these:
{proposition_list}

INSTRUCTIONS:
- Generate only mundane, innocuous day-to-day content (scheduling, small talk, routine work).
- Every artifact must be authored by one of the listed personas using a listed device.
- Timestamps must be within the master timeline bounds (hours from start: 0 to {total_hours:.1f}).
- Do not reference any taboo topic or true proposition in any form.
- Output ONLY valid JSON — no markdown, no extra text.
"""

_NOISE_USER_TMPL = """\
Generate exactly {batch_size} noise artifacts for the '{profile}' profile.

Return a JSON object with key "artifacts" containing exactly {batch_size} objects.
Each object must have:
  "text_content"           – string, main readable text (1–6 sentences)
  "owner"                  – string, one of the persona names above
  "device"                 – string, a device_id owned by that persona
  "timestamp_offset_hours" – number in [0, {total_hours:.1f}]
  "metadata"               – object with profile-specific fields:
      email  → {{"subject": "...", "from": "...", "to": "..."}}
      sms    → {{"sender": "...", "recipient": "...", "timestamp": "..."}}
      pdf    → {{"author": "...", "created": "D:YYYYMMDDHHMMSS"}}
      xlsx   → {{"author": "...", "sheet_title": "...", "timestamp": "..."}}
      jpeg   → {{"datetime": "YYYY:MM:DD HH:MM:SS", "device": "..."}}
      log    → {{"source": "...", "timestamp": "YYYY-MM-DD HH:MM:SS"}}
"""

_GUARD_SYSTEM_TMPL = """\
You are a strict evidence guardian for the Evidence Factory system.
Your task is to identify noise artifacts that accidentally leak or contradict case facts.

TRUE PROPOSITIONS (must not appear in any noise artifact in any form):
{proposition_list}

For each artifact, respond "REJECT" if it:
  1. Directly states or implies a true proposition
  2. Contradicts a true proposition
  3. Contains suspiciously specific information matching key case facts

Respond "PASS" if the artifact is innocuous and unrelated to the case facts.
Output ONLY valid JSON — no markdown, no extra text.
"""

_GUARD_USER_TMPL = """\
Check each artifact below. Return a JSON object with key "results" containing an array.
Each element: {{"index": <int>, "verdict": "REJECT"|"PASS", "reason": "<brief>"}}.

ARTIFACTS:
{artifacts_text}
"""


def _first_text(content: Any, default: str) -> str:
    """Return the text of the first content block that has a .text attribute."""
    for block in content or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            return text
    return default


class LLMGateway:
    """Anthropic SDK wrapper with prompt caching and noise batching."""

    def __init__(self, client: anthropic.Anthropic | None = None) -> None:
        self._client = client or anthropic.Anthropic()
        self.cache_hits = 0
        self.cache_misses = 0

    # ------------------------------------------------------------------
    # Noise generation
    # ------------------------------------------------------------------

    def generate_noise_batch(
        self,
        persona_context: str,
        timeline_start: str,
        timeline_end: str,
        total_hours: float,
        taboo_topics: list[str],
        true_propositions: list[str],
        profile: str,
        case_description: str = "",
        batch_size: int = 10,
    ) -> list[dict[str, Any]]:
        """Generate batch_size noise artifacts for *profile* via Haiku."""
        system_text = _NOISE_SYSTEM_TMPL.format(
            case_description=case_description or "A fictional investigation case.",
            persona_context=persona_context,
            timeline_start=timeline_start,
            timeline_end=timeline_end,
            total_hours=total_hours,
            taboo_list="\n".join(f"- {t}" for t in taboo_topics) or "- (none)",
            proposition_list="\n".join(f"{i + 1}. {p}" for i, p in enumerate(true_propositions))
            or "- (none)",
        )
        user_text = _NOISE_USER_TMPL.format(
            batch_size=batch_size,
            profile=profile,
            total_hours=total_hours,
        )
        response = self._client.messages.create(
            model=_HAIKU_MODEL,
            max_tokens=4096,
            system=[
                {
                    "type": "text",
                    "text": system_text,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_text}],
        )
        self._record_cache_usage(response.usage)
        raw_text = _first_text(response.content, "{}")
        return self._parse_noise_response(raw_text)

    # ------------------------------------------------------------------
    # Guard LLM pass
    # ------------------------------------------------------------------

    def guard_check_batch(
        self,
        artifact_texts: list[str],
        true_propositions: list[str],
    ) -> list[bool]:
        """Return True (REJECT) for each artifact that leaks/contradicts truth."""
        if not artifact_texts:
            return []

        system_text = _GUARD_SYSTEM_TMPL.format(
            proposition_list="\n".join(f"{i + 1}. {p}" for i, p in enumerate(true_propositions))
            or "- (none)",
        )
        artifacts_text = "\n\n".join(f"{i}: {text[:500]}" for i, text in enumerate(artifact_texts))
        user_text = _GUARD_USER_TMPL.format(artifacts_text=artifacts_text)

        response = self._client.messages.create(
            model=_HAIKU_MODEL,
            max_tokens=2048,
            system=[
                {
                    "type": "text",
                    "text": system_text,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_text}],
        )
        self._record_cache_usage(response.usage)
        raw_text = _first_text(response.content, '{"results": []}')
        return self._parse_guard_response(raw_text, len(artifact_texts))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _record_cache_usage(self, usage: Any) -> None:
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        if cache_read > 0:
            self.cache_hits += 1
            logger.info("Cache hit: %d tokens read from prompt cache", cache_read)
        else:
            self.cache_misses += 1

    def _parse_noise_response(self, raw: str) -> list[dict[str, Any]]:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Noise LLM returned invalid JSON; skipping batch")
            return []
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict) and "artifacts" in data:
            items = data["artifacts"]
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
        logger.warning("Noise LLM response has unexpected shape; skipping batch")
        return []

    def _parse_guard_response(self, raw: str, expected: int) -> list[bool]:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Guard LLM returned invalid JSON; defaulting all to PASS")
            return [False] * expected

        results_raw: Any = None
        if isinstance(data, list):
            results_raw = data
        elif isinstance(data, dict) and "results" in data:
            results_raw = data["results"]

        if not isinstance(results_raw, list):
            return [False] * expected

        verdicts = [False] * expected
        for item in results_raw:
            if not isinstance(item, dict):
                continue
            idx = item.get("index")
            verdict = item.get("verdict", "PASS")
            if isinstance(idx, int) and 0 <= idx < expected:
                verdicts[idx] = str(verdict).upper() == "REJECT"
        return verdicts
