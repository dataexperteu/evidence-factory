"""Unit tests for cast_extractor.extract_cast."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from api.pipeline.cast_extractor import extract_cast
from api.pipeline.llm_gateway import LLMGateway
from api.pipeline.source_intake import SourceText


def _source(body: str = "Alice met Bob at the park. Carol watched from a distance.") -> SourceText:
    return SourceText(body=body, char_count=len(body))


def _gateway_returning(text: str) -> LLMGateway:
    gw = MagicMock(spec=LLMGateway)
    gw.complete.return_value = text
    return gw


# ---------------------------------------------------------------------------
# Happy path — fixture mode (LLMGateway with mode="fixture")
# ---------------------------------------------------------------------------


def test_fixture_mode_returns_persona_registry():
    """Fixture gateway returns valid cast JSON; registry is populated."""
    gw = LLMGateway(mode="fixture")
    reg = extract_cast(_source(), gateway=gw)
    assert len(reg.personas()) >= 2


def test_fixture_mode_returns_at_least_three_fixture_personas():
    """Fixture cast response embeds three hardcoded personas."""
    gw = LLMGateway(mode="fixture")
    reg = extract_cast(_source(), gateway=gw)
    # Fixture response has Alice, Bob, Carol
    ids = reg.persona_ids()
    assert "p_alice_fixture" in ids
    assert "p_bob_fixture" in ids
    assert "p_carol_fixture" in ids


def test_fixture_mode_system_actors_present():
    gw = LLMGateway(mode="fixture")
    reg = extract_cast(_source(), gateway=gw)
    assert reg.is_system_actor("sys_bldg_access")
    assert reg.is_system_actor("sys_pbx")


def test_fixture_mode_email_persona_guarantee():
    gw = LLMGateway(mode="fixture")
    reg = extract_cast(_source(), gateway=gw)
    email_owners = {
        d.owner_id for p in reg.personas() for d in reg.devices_for(p.id) if "email" in d.profiles
    }
    assert len(email_owners) >= 2, f"expected ≥2 email-device owners, got {email_owners}"


# ---------------------------------------------------------------------------
# JSON fallback — malformed LLM response
# ---------------------------------------------------------------------------


def test_malformed_json_triggers_fallback_not_exception():
    gw = _gateway_returning("this is not json at all {{{")
    reg = extract_cast(_source(), gateway=gw)
    assert len(reg.personas()) >= 2


def test_fallback_registry_has_system_actors():
    gw = _gateway_returning("NOT JSON")
    reg = extract_cast(_source(), gateway=gw)
    assert reg.is_system_actor("sys_bldg_access")
    assert reg.is_system_actor("sys_pbx")


def test_fallback_registry_satisfies_email_guarantee():
    gw = _gateway_returning("NOT JSON")
    reg = extract_cast(_source(), gateway=gw)
    email_owners = {
        d.owner_id for p in reg.personas() for d in reg.devices_for(p.id) if "email" in d.profiles
    }
    assert len(email_owners) >= 2


def test_empty_personas_array_triggers_fallback():
    gw = _gateway_returning(json.dumps({"personas": []}))
    reg = extract_cast(_source(), gateway=gw)
    assert len(reg.personas()) >= 2


# ---------------------------------------------------------------------------
# Email-persona guarantee — custom scenarios
# ---------------------------------------------------------------------------


def test_single_persona_gets_second_synthesised():
    """When LLM returns one persona, a second is synthesised."""
    payload = json.dumps({"personas": [{"display_name": "Lone Wolf", "role": "protagonist"}]})
    gw = _gateway_returning(payload)
    reg = extract_cast(_source(), gateway=gw)
    assert len(reg.personas()) >= 2


def test_email_guarantee_with_one_llm_persona():
    """Even with only one LLM persona the email-device minimum is met."""
    payload = json.dumps({"personas": [{"display_name": "Lone Wolf", "role": "protagonist"}]})
    gw = _gateway_returning(payload)
    reg = extract_cast(_source(), gateway=gw)
    email_owners = {
        d.owner_id for p in reg.personas() for d in reg.devices_for(p.id) if "email" in d.profiles
    }
    assert len(email_owners) >= 2


def test_email_guarantee_with_multiple_llm_personas():
    """When LLM returns multiple personas the email minimum is still met."""
    payload = json.dumps(
        {
            "personas": [
                {"display_name": "Alice", "role": "protagonist"},
                {"display_name": "Bob", "role": "antagonist"},
                {"display_name": "Carol", "role": "witness"},
            ]
        }
    )
    gw = _gateway_returning(payload)
    reg = extract_cast(_source(), gateway=gw)
    email_owners = {
        d.owner_id for p in reg.personas() for d in reg.devices_for(p.id) if "email" in d.profiles
    }
    assert len(email_owners) >= 2


# ---------------------------------------------------------------------------
# System actor presence
# ---------------------------------------------------------------------------


def test_system_actors_are_always_appended():
    """System actors appear regardless of LLM output."""
    for payload in [
        json.dumps({"personas": [{"display_name": "X", "role": "r"}]}),
        "bad json",
        json.dumps({"personas": []}),
    ]:
        gw = _gateway_returning(payload)
        reg = extract_cast(_source(), gateway=gw)
        assert reg.is_system_actor("sys_bldg_access"), (
            f"missing sys_bldg_access for payload={payload!r}"
        )
        assert reg.is_system_actor("sys_pbx"), f"missing sys_pbx for payload={payload!r}"


def test_system_devices_owned_by_system_actors():
    gw = LLMGateway(mode="fixture")
    reg = extract_cast(_source(), gateway=gw)
    sys_devs = reg.system_devices()
    assert len(sys_devs) == 2
    for dev in sys_devs:
        assert reg.is_system_actor(dev.owner_id)
        assert "system_log_csv" in dev.profiles


# ---------------------------------------------------------------------------
# Persona ID and email address synthesis
# ---------------------------------------------------------------------------


def test_persona_ids_are_slugified():
    payload = json.dumps(
        {
            "personas": [
                {"display_name": "Jane Doe", "role": "protagonist"},
                {"display_name": "John Smith", "role": "antagonist"},
            ]
        }
    )
    gw = _gateway_returning(payload)
    reg = extract_cast(_source(), gateway=gw)
    ids = reg.persona_ids()
    assert "p_jane_doe" in ids
    assert "p_john_smith" in ids


def test_persona_email_addresses_contain_story_slug():
    body = "Once upon a time"
    payload = json.dumps({"personas": [{"display_name": "Jane Doe", "role": "r"}]})
    gw = _gateway_returning(payload)
    reg = extract_cast(SourceText(body=body, char_count=len(body)), gateway=gw)
    persona = reg.get_persona("p_jane_doe")
    assert "@" in persona.email_address
    assert ".example" in persona.email_address


# ---------------------------------------------------------------------------
# LLM gateway call contract
# ---------------------------------------------------------------------------


def test_gateway_complete_called_with_cast_role():
    gw = _gateway_returning(json.dumps({"personas": [{"display_name": "A", "role": "r"}]}))
    src = _source()
    extract_cast(src, gateway=gw)
    gw.complete.assert_called_once()
    call_args = gw.complete.call_args
    assert call_args[0][0] == "cast"


def test_gateway_complete_uses_char_count_cache_key():
    gw = _gateway_returning(json.dumps({"personas": [{"display_name": "A", "role": "r"}]}))
    src = _source("hello world")
    extract_cast(src, gateway=gw)
    _, kwargs = gw.complete.call_args
    assert kwargs.get("cache_key") == f"cast::{src.char_count}"
