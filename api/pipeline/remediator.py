"""Remediator.

When the Smoking-Gun Critic flags a load-bearing artifact as `too_strong`,
this module applies one of the slice-8 transforms (split, dilute) to defuse
it. The strategy *selector* is injectable (LLM choice or seeded RNG); the
*transforms themselves* are deterministic given a strategy and the input
artifact, so unit tests can inject a strategy and assert the output shape.

Transforms (PRD-locked):
- **split**: produce N ≥ 2 owner-distinct fragments whose combined signal
  preserves corroboration for the bound proposition. Slice-8 ships N=2.
- **dilute**: produce a single artifact whose damning span is softened/
  buried in unrelated context while still binding the original proposition.

Out of scope for slice 8: the remaining two strategies (`redact-relocate`,
`demote-to-red-herring`) ship in later slices and are not registered here.
"""

from __future__ import annotations

import hashlib
import random
import secrets
from dataclasses import dataclass

from ..provenance.email_profile import EmailBrief, write_email
from .llm_gateway import LLMGateway
from .persona_registry import PersonaRegistry
from .types import Artifact

SPLIT = "split"
DILUTE = "dilute"
SUPPORTED_STRATEGIES: tuple[str, ...] = (SPLIT, DILUTE)


class RemediationError(Exception):
    pass


@dataclass(frozen=True)
class RemediationOutput:
    """Result of one remediation transform.

    `new_artifacts` are the replacements for the flagged input; the caller
    is responsible for removing the original from the artifact list and the
    Signal Ledger, then recording each new artifact.
    """

    new_artifacts: list[Artifact]
    strategy: str


def choose_strategy(rng: random.Random) -> str:
    """Pick a strategy uniformly from the supported set."""
    return rng.choice(list(SUPPORTED_STRATEGIES))


def _next_id(original_id: str, suffix: str) -> str:
    # Salt with a short random hex so re-running on the same artifact id
    # (e.g. across remediation rounds) produces fresh, unique ids.
    h = hashlib.sha256(f"{original_id}::{suffix}::{secrets.token_hex(2)}".encode()).hexdigest()[:6]
    return f"{original_id}__{suffix}_{h}"


def _pick_alternate_owner(registry: PersonaRegistry, exclude_owner_id: str) -> tuple[str, str]:
    """Pick a different persona that owns an email-capable device.

    Returns (persona_id, device_id). Raises if no alternate exists — that
    is a registry-config error, not a remediation bug.
    """
    for persona in registry.personas():
        if persona.id == exclude_owner_id:
            continue
        for device in registry.devices_for(persona.id):
            if device.profile == "email":
                return persona.id, device.id
    raise RemediationError(
        f"no alternate email-capable owner available (excluding {exclude_owner_id})"
    )


def split(
    artifact: Artifact,
    registry: PersonaRegistry,
    *,
    gateway: LLMGateway,
    disclaimer: str,
) -> RemediationOutput:
    """Produce two owner-distinct email fragments.

    Both fragments bind the same propositions as the original (preserving
    corroboration in aggregate) but each carries half the signal weight
    and a deliberately fragmentary subject/body. The first fragment keeps
    the original owner; the second uses an alternate persona's email device
    so the pair is owner-distinct.
    """
    if not artifact.bound_proposition_ids:
        raise RemediationError(f"cannot split artifact {artifact.id}: no bound propositions")

    first_persona = registry.get_persona(artifact.owner_id)
    second_owner_id, second_device_id = _pick_alternate_owner(registry, artifact.owner_id)
    second_persona = registry.get_persona(second_owner_id)

    prop_id = artifact.bound_proposition_ids[0]
    body_a = gateway.complete(
        "artifact_content",
        f"Draft a brief, oblique email mentioning only fragmentary context for {prop_id}",
        cache_key=f"split::a::{artifact.id}",
    )
    body_b = gateway.complete(
        "artifact_content",
        f"Draft a brief, oblique email mentioning a separate fragmentary detail for {prop_id}",
        cache_key=f"split::b::{artifact.id}",
    )

    brief_a = EmailBrief(
        sender_name=first_persona.display_name,
        sender_address=first_persona.email_address,
        recipients=((second_persona.display_name, second_persona.email_address),),
        subject=f"Re: {prop_id} fragment a",
        body=body_a,
        sent_at=artifact.acquisition_time,
    )
    brief_b = EmailBrief(
        sender_name=second_persona.display_name,
        sender_address=second_persona.email_address,
        recipients=((first_persona.display_name, first_persona.email_address),),
        subject=f"Re: {prop_id} fragment b",
        body=body_b,
        sent_at=artifact.acquisition_time,
    )
    written_a = write_email(brief_a, disclaimer=disclaimer)
    written_b = write_email(brief_b, disclaimer=disclaimer)

    new_weight = artifact.signal_weight * 0.5
    art_a = Artifact(
        id=_next_id(artifact.id, "split_a"),
        owner_id=first_persona.id,
        device_id=artifact.device_id,
        profile="email",
        filename=written_a.filename,
        payload=written_a.payload,
        sha256=written_a.sha256,
        acquisition_time=artifact.acquisition_time,
        bound_proposition_ids=artifact.bound_proposition_ids,
        signal_weight=new_weight,
    )
    art_b = Artifact(
        id=_next_id(artifact.id, "split_b"),
        owner_id=second_persona.id,
        device_id=second_device_id,
        profile="email",
        filename=written_b.filename,
        payload=written_b.payload,
        sha256=written_b.sha256,
        acquisition_time=artifact.acquisition_time,
        bound_proposition_ids=artifact.bound_proposition_ids,
        signal_weight=new_weight,
    )
    return RemediationOutput(new_artifacts=[art_a, art_b], strategy=SPLIT)


def dilute(
    artifact: Artifact,
    registry: PersonaRegistry,
    *,
    gateway: LLMGateway,
    disclaimer: str,
) -> RemediationOutput:
    """Produce a single email with the damning span softened and buried.

    Same owner, same device, same bound propositions; only the content is
    rewritten to a meandering thread that mentions the proposition obliquely
    among unrelated administrivia. Signal weight is halved.
    """
    if not artifact.bound_proposition_ids:
        raise RemediationError(f"cannot dilute artifact {artifact.id}: no bound propositions")
    persona = registry.get_persona(artifact.owner_id)
    # Recipient: any non-owner persona (deterministic pick — the registry's
    # persona order is stable).
    recipient_persona = next(
        (p for p in registry.personas() if p.id != persona.id),
        None,
    )
    if recipient_persona is None:
        raise RemediationError(
            f"dilute requires a non-owner recipient for {artifact.id}; registry has none"
        )

    prop_id = artifact.bound_proposition_ids[0]
    softened_body = gateway.complete(
        "artifact_content",
        f"Draft a longer, meandering email that buries any reference to {prop_id} "
        "inside unrelated administrivia and small talk",
        cache_key=f"dilute::{artifact.id}",
    )
    brief = EmailBrief(
        sender_name=persona.display_name,
        sender_address=persona.email_address,
        recipients=((recipient_persona.display_name, recipient_persona.email_address),),
        subject=f"Re: {prop_id} (longer thread)",
        body=softened_body,
        sent_at=artifact.acquisition_time,
    )
    written = write_email(brief, disclaimer=disclaimer)
    diluted = Artifact(
        id=_next_id(artifact.id, "dilute"),
        owner_id=persona.id,
        device_id=artifact.device_id,
        profile="email",
        filename=written.filename,
        payload=written.payload,
        sha256=written.sha256,
        acquisition_time=artifact.acquisition_time,
        bound_proposition_ids=artifact.bound_proposition_ids,
        signal_weight=artifact.signal_weight * 0.5,
    )
    return RemediationOutput(new_artifacts=[diluted], strategy=DILUTE)


def remediate(
    artifact: Artifact,
    registry: PersonaRegistry,
    *,
    strategy: str,
    gateway: LLMGateway,
    disclaimer: str,
) -> RemediationOutput:
    """Dispatch to the requested strategy's transform."""
    if strategy == SPLIT:
        return split(artifact, registry, gateway=gateway, disclaimer=disclaimer)
    if strategy == DILUTE:
        return dilute(artifact, registry, gateway=gateway, disclaimer=disclaimer)
    raise RemediationError(f"unknown strategy {strategy!r}; supported: {SUPPORTED_STRATEGIES}")
