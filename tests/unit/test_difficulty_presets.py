"""Unit tests for difficulty preset definitions and settings_for_preset()."""

from __future__ import annotations

import pytest

from api.pipeline.orchestrator import RunSettings, settings_for_preset


def test_easy_preset_values() -> None:
    s = settings_for_preset("easy")
    assert s.min_owner_distinct == 2
    assert s.owners_per_proposition == 2
    assert s.fragmentation_factor == 2
    assert s.dominance_margin == pytest.approx(0.5)
    assert s.target_artifact_count == 100
    assert s.red_herring_count == 1
    assert s.noise_count == 80


def test_medium_preset_values() -> None:
    s = settings_for_preset("medium")
    assert s.min_owner_distinct == 3
    assert s.owners_per_proposition == 3
    assert s.fragmentation_factor == 3
    assert s.dominance_margin == pytest.approx(0.3)
    assert s.target_artifact_count == 400
    assert s.red_herring_count == 3
    assert s.noise_count == 350


def test_hard_preset_values() -> None:
    s = settings_for_preset("hard")
    assert s.min_owner_distinct == 5
    assert s.owners_per_proposition == 5
    assert s.fragmentation_factor == 5
    assert s.dominance_margin == pytest.approx(0.15)
    assert s.target_artifact_count == 1000
    assert s.red_herring_count == 6
    assert s.noise_count == 950


def test_settings_for_preset_returns_runsettings_instance() -> None:
    for preset in ("easy", "medium", "hard"):
        assert isinstance(settings_for_preset(preset), RunSettings)  # type: ignore[arg-type]


def test_invalid_preset_raises_value_error() -> None:
    with pytest.raises(ValueError, match="unknown difficulty preset"):
        settings_for_preset("extreme")  # type: ignore[arg-type]


def test_hard_harder_than_easy() -> None:
    easy = settings_for_preset("easy")
    hard = settings_for_preset("hard")
    # More artifacts and corroborators required
    assert hard.owners_per_proposition > easy.owners_per_proposition
    assert hard.min_owner_distinct > easy.min_owner_distinct
    assert hard.fragmentation_factor > easy.fragmentation_factor
    # Tighter dominance margin (smaller value = harder for attacker)
    assert hard.dominance_margin < easy.dominance_margin
    # ≥5× target artifact count
    assert hard.target_artifact_count >= 5 * easy.target_artifact_count
    # ≥2× red herrings
    assert hard.red_herring_count >= 2 * easy.red_herring_count
    # More noise
    assert hard.noise_count > easy.noise_count


def test_medium_between_easy_and_hard() -> None:
    easy = settings_for_preset("easy")
    medium = settings_for_preset("medium")
    hard = settings_for_preset("hard")
    assert easy.owners_per_proposition < medium.owners_per_proposition < hard.owners_per_proposition
    assert easy.fragmentation_factor < medium.fragmentation_factor < hard.fragmentation_factor
    assert easy.dominance_margin > medium.dominance_margin > hard.dominance_margin
    assert easy.target_artifact_count < medium.target_artifact_count < hard.target_artifact_count
    assert easy.red_herring_count < medium.red_herring_count < hard.red_herring_count
    assert easy.noise_count < medium.noise_count < hard.noise_count


def test_preset_returns_independent_instances() -> None:
    s1 = settings_for_preset("medium")
    s2 = settings_for_preset("medium")
    s1.owners_per_proposition = 99
    assert s2.owners_per_proposition == 3  # mutations don't leak between calls
