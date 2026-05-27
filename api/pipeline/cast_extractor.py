"""Cast Extractor — extract named characters from source text via LLM.

SourceText → PersonaRegistry with LLM-derived personas, synthesised device
profiles, and the two mandatory system actors (sys_bldg_access, sys_pbx).
"""

from __future__ import annotations

import json
import logging
import random
import re
from typing import Any, Literal

from .llm_gateway import LLMGateway
from .persona_registry import PersonaRegistry, SystemActor
from .source_intake import SourceText
from .types import Device, Persona

LOG = logging.getLogger("evidence_factory.cast_extractor")

_SLUG_RE = re.compile(r"[^a-z0-9]+")

_DeviceProfile = Literal["email", "pdf", "xlsx_ledger", "jpeg", "system_log_csv", "sms"]

# Profiles available for persona devices (system_log_csv is system-only).
_PERSONA_PROFILES: list[_DeviceProfile] = ["email", "pdf", "sms", "jpeg", "xlsx_ledger"]

_SYSTEM_ACTORS: list[SystemActor] = [
    SystemActor(id="sys_bldg_access", label="building-access-controller", log_schema="access_log"),
    SystemActor(id="sys_pbx", label="phone-exchange", log_schema="cdr"),
]

_SYSTEM_DEVICES: list[Device] = [
    Device(
        id="d_bldg_ctrl",
        owner_id="sys_bldg_access",
        label="building-controller",
        profile="system_log_csv",
    ),
    Device(
        id="d_pbx",
        owner_id="sys_pbx",
        label="phone-exchange",
        profile="system_log_csv",
    ),
]

_FALLBACK_PERSONAS: list[dict[str, str]] = [
    {"display_name": "Unknown Protagonist", "role": "protagonist"},
    {"display_name": "Unknown Correspondent", "role": "correspondent"},
]


def _slugify(name: str, max_len: int = 30) -> str:
    s = _SLUG_RE.sub("_", name.lower()).strip("_")
    return s[:max_len] or "person"


def _story_slug(source: SourceText) -> str:
    words = source.body.split()[:4]
    s = _SLUG_RE.sub("_", " ".join(words).lower()).strip("_")
    return s[:30] or "story"


def _build_personas(raw_list: list[dict[str, Any]], story_slug: str) -> list[Persona]:
    """Synthesise Persona objects from LLM-returned dicts, deduplicating slugs."""
    slug_counts: dict[str, int] = {}
    personas: list[Persona] = []
    for raw in raw_list:
        display_name = str(raw.get("display_name") or "Unknown Person").strip() or "Unknown Person"
        base_slug = _slugify(display_name)
        count = slug_counts.get(base_slug, 0)
        slug_counts[base_slug] = count + 1
        slug = base_slug if count == 0 else f"{base_slug}_{count}"
        personas.append(
            Persona(
                id=f"p_{slug}",
                display_name=display_name,
                email_address=f"{slug}@{story_slug}.example",
            )
        )
    return personas


def _assign_devices(personas: list[Persona], rng: random.Random) -> list[Device]:
    """Assign 1–3 devices per persona; guarantee ≥2 personas own an email device."""
    devices: list[Device] = []
    for persona in personas:
        slug = persona.id[2:]  # strip "p_" prefix for device IDs
        count = rng.randint(1, 3)
        profiles = rng.sample(_PERSONA_PROFILES, min(count, len(_PERSONA_PROFILES)))
        for profile in profiles:
            devices.append(
                Device(
                    id=f"d_{slug}_{profile}",
                    owner_id=persona.id,
                    label=f"{slug}-{profile}",
                    profile=profile,
                )
            )

    # Guarantee ≥2 personas own an email device.
    email_owners = {d.owner_id for d in devices if d.profile == "email"}
    for persona in personas:
        if len(email_owners) >= 2:
            break
        if persona.id not in email_owners:
            slug = persona.id[2:]
            device_id = f"d_{slug}_email"
            # Remove any duplicate (e.g. if same id was already assigned differently)
            devices = [d for d in devices if d.id != device_id]
            devices.append(
                Device(
                    id=device_id,
                    owner_id=persona.id,
                    label=f"{slug}-email",
                    profile="email",
                )
            )
            email_owners.add(persona.id)

    return devices


def extract_cast(source: SourceText, *, gateway: LLMGateway) -> PersonaRegistry:
    """Extract named characters from *source* and return a fully initialised PersonaRegistry.

    Calls the LLM via *gateway* to identify characters, synthesises persona IDs
    and email addresses from their names, randomly assigns 1–3 device profiles
    per persona, and appends the two mandatory system actors with their
    system_log_csv devices.

    If the LLM returns unparseable JSON the function falls back to a minimal
    2-persona cast rather than raising.  If fewer than 2 characters are extracted
    a generic "Unknown Correspondent" persona is synthesised to satisfy the
    at-least-2-email-persona invariant.
    """
    prompt = (
        "Extract all named characters from the following story. "
        'Return JSON with this exact shape: {"personas": [{"display_name": "...", "role": "..."}]}\n\n'
        f"{source.body[:3000]}"
    )
    raw_text = gateway.complete("cast", prompt, cache_key=f"cast::{source.char_count}")

    persona_dicts: list[dict[str, Any]] = []
    try:
        data = json.loads(raw_text)
        if isinstance(data, dict) and isinstance(data.get("personas"), list):
            persona_dicts = [p for p in data["personas"] if isinstance(p, dict)]
    except (json.JSONDecodeError, ValueError):
        LOG.warning("cast LLM returned invalid JSON; using fallback cast")

    if not persona_dicts:
        LOG.warning("cast LLM returned no usable personas; using fallback cast")
        persona_dicts = list(_FALLBACK_PERSONAS)

    story_slug = _story_slug(source)
    personas = _build_personas(persona_dicts, story_slug)

    # Ensure at least 2 personas so the email-persona guarantee can be satisfied.
    if len(personas) < 2:
        fallback_slug = _slugify("Unknown Correspondent")
        fallback_id = f"p_{fallback_slug}"
        if not any(p.id == fallback_id for p in personas):
            personas.append(
                Persona(
                    id=fallback_id,
                    display_name="Unknown Correspondent",
                    email_address=f"{fallback_slug}@{story_slug}.example",
                )
            )
        else:
            personas.append(
                Persona(
                    id="p_unknown_correspondent_2",
                    display_name="Unknown Correspondent",
                    email_address=f"unknown_correspondent_2@{story_slug}.example",
                )
            )

    rng = random.Random(source.char_count)
    devices = _assign_devices(personas, rng)

    return PersonaRegistry(
        personas,
        devices + list(_SYSTEM_DEVICES),
        system_actors=list(_SYSTEM_ACTORS),
    )
