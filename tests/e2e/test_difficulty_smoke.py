"""Slice 12: Difficulty preset smoke tests.

Verifies:
- The pipeline completes successfully at each preset level (easy/medium/hard).
- Hard produces measurably more artifacts than Easy.
- Preset parameters satisfy the ≥5× target count and ≥2× red-herring ratios.
"""

from __future__ import annotations

from pathlib import Path

from api.pipeline.orchestrator import run_sync, settings_for_preset

FIXTURE = Path(__file__).parent / "fixtures" / "speckled_band_excerpt.txt"


def _run_preset(preset: str) -> int:
    paste = FIXTURE.read_text(encoding="utf-8")
    settings = settings_for_preset(preset)  # type: ignore[arg-type]
    events, result = run_sync(paste, attestation_checked=True, settings=settings)
    failed = [e for e in events if e.status == "failed"]
    assert not failed, f"preset={preset!r} emitted failure events: {failed}"
    assert result is not None, f"preset={preset!r} did not produce a result"
    return result.artifact_count


def test_easy_preset_pipeline_completes() -> None:
    count = _run_preset("easy")
    assert count > 0


def test_medium_preset_pipeline_completes() -> None:
    count = _run_preset("medium")
    assert count > 0


def test_hard_preset_pipeline_completes() -> None:
    count = _run_preset("hard")
    assert count > 0


def test_hard_produces_more_artifacts_than_easy() -> None:
    """Hard vs Easy produces measurably different corpora."""
    easy_count = _run_preset("easy")
    hard_count = _run_preset("hard")
    assert hard_count > easy_count, (
        f"Hard ({hard_count}) should produce more artifacts than Easy ({easy_count})"
    )


def test_preset_parameters_satisfy_ratio_requirements() -> None:
    """Validate the preset parameter ratios without running the full pipeline."""
    easy = settings_for_preset("easy")
    hard = settings_for_preset("hard")
    # ≥5× target artifact count
    assert hard.target_artifact_count >= 5 * easy.target_artifact_count
    # ≥2× red herring count
    assert hard.red_herring_count >= 2 * easy.red_herring_count
    # Tighter dominance margin (smaller value)
    assert hard.dominance_margin < easy.dominance_margin
