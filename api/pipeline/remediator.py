"""Remediator — defuses artifacts the Smoking-Gun Critic flags `too_strong`.

This module ships all four PRD strategies:

- ``split``  — fan the artifact out into >= 2 owner-distinct fragments whose
  combined signal preserves corroboration for the bound proposition(s). Each
  fragment carries a fraction of the original weight, so no single fragment
  clears the smoking-gun bar.
- ``dilute`` — keep a single artifact but soften / bury the damning span and
  scale its signal weight down below the bar.
- ``redact_relocate`` — surgically remove the damning span from the flagged
  artifact and re-emit it elsewhere as a weaker corroborator on a *different*
  owner's device.
- ``demote`` — mutate the artifact so its damning claim becomes subtly false /
  misattributed (a contradicted lead). The true signal is re-emitted weakly on
  a different owner; the demoted artifact is registered as a red herring and a
  breaker bundle is scheduled for it (built by the Red-Herring & Breaker
  Designer). ``demote`` returns a :class:`DemoteResult` rather than a plain
  artifact list because it has these extra effects; the bounded re-critic loop
  in ``remediation.run_critique_pass`` drives it.

The strategy *selector* is injectable (seeded RNG in production, fixed in
tests); the *transforms* are deterministic in shape given a strategy and a
candidate artifact (only the email writer's random filename suffix varies).
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from datetime import datetime
from email.parser import BytesParser
from email.policy import default as default_policy
from email.utils import getaddresses, parseaddr
from typing import Literal, Protocol

from ..provenance.email_profile import EmailBrief, write_email
from .llm_gateway import SMOKING_GUN_WEIGHT_THRESHOLD
from .persona_registry import PersonaRegistry
from .types import Artifact, Persona, Proposition

Strategy = Literal["split", "dilute", "redact_relocate", "demote"]

STRATEGIES: tuple[Strategy, ...] = ("split", "dilute", "redact_relocate", "demote")

# A diluted artifact keeps half its weight; two fragments each take half on a
# split. Both land below SMOKING_GUN_WEIGHT_THRESHOLD (1.0), so the re-critic
# pass clears them in one round.
DILUTE_FACTOR = 0.5
SPLIT_FRAGMENTS = 2

assert DILUTE_FACTOR < SMOKING_GUN_WEIGHT_THRESHOLD


class StrategySelector(Protocol):
    def choose(self, artifact: Artifact) -> Strategy: ...


@dataclass(frozen=True)
class RandomStrategySelector:
    """Picks `split` or `dilute` per flagged artifact (PRD: random per instance)."""

    rng: random.Random

    def choose(self, artifact: Artifact) -> Strategy:
        return self.rng.choice(STRATEGIES)


@dataclass(frozen=True)
class FixedStrategySelector:
    """Forces a strategy — used by unit tests to pin transform shapes."""

    strategy: Strategy

    def choose(self, artifact: Artifact) -> Strategy:
        return self.strategy


@dataclass(frozen=True)
class _ParsedEmail:
    sender_name: str
    sender_address: str
    recipients: tuple[tuple[str, str], ...]
    subject: str
    body: str


def _parse_email(payload: bytes) -> _ParsedEmail:
    msg = BytesParser(policy=default_policy).parsebytes(payload)
    sender_name, sender_address = parseaddr(str(msg["From"] or ""))
    recipients = tuple((name, addr) for name, addr in getaddresses([str(msg["To"] or "")]) if addr)
    body = msg.get_content() if msg.get_content_type() == "text/plain" else str(msg.get_payload())
    return _ParsedEmail(
        sender_name=sender_name or "Unknown",
        sender_address=sender_address or "unknown@evidence-factory.example",
        recipients=recipients,
        subject=str(msg["Subject"] or "(no subject)"),
        body=body.strip(),
    )


def _write_artifact(
    *,
    artifact_id: str,
    owner: Persona,
    device_id: str,
    recipients: tuple[tuple[str, str], ...],
    subject: str,
    body: str,
    sent_at: datetime,
    bound_proposition_ids: tuple[str, ...],
    signal_weight: float,
    disclaimer: str,
) -> Artifact:
    brief = EmailBrief(
        sender_name=owner.display_name,
        sender_address=owner.email_address,
        recipients=recipients,
        subject=subject,
        body=body,
        sent_at=sent_at,
    )
    written = write_email(brief, disclaimer=disclaimer)
    return Artifact(
        id=artifact_id,
        owner_id=owner.id,
        device_id=device_id,
        profile="email",
        filename=written.filename,
        payload=written.payload,
        sha256=written.sha256,
        acquisition_time=sent_at,
        bound_proposition_ids=bound_proposition_ids,
        signal_weight=signal_weight,
    )


def _recipient_for(registry: PersonaRegistry, owner_id: str) -> tuple[str, str]:
    for persona in registry.personas():
        if persona.id != owner_id:
            return persona.display_name, persona.email_address
    raise ValueError("registry must have at least two personas to emit email")


def _email_persona_and_device(registry: PersonaRegistry, *, exclude_id: str) -> tuple[Persona, str]:
    """First email-capable persona (and its email device) other than ``exclude_id``."""
    for persona in registry.personas():
        if persona.id == exclude_id:
            continue
        for device in registry.devices_for(persona.id):
            if device.profile == "email":
                return persona, device.id
    raise ValueError("registry must have a second email-capable persona to relocate to")


_PROP_ID_SAFE = re.compile(r"[^a-zA-Z0-9_]+")


def false_proposition_id_for(artifact: Artifact) -> str:
    """Deterministic id for the red herring a ``demote`` of this artifact creates."""
    return f"prop_rh_{_PROP_ID_SAFE.sub('_', artifact.id)}"


def dilute(artifact: Artifact, registry: PersonaRegistry, *, disclaimer: str) -> list[Artifact]:
    """Soften / bury the damning span and scale the signal weight down.

    Returns a single artifact owned by the same persona/device as the original.
    """
    parsed = _parse_email(artifact.payload)
    owner = registry.get_persona(artifact.owner_id)
    softened = (
        "Quick note among a few other bits and pieces.\n\n"
        "For what it's worth, and I might be misremembering some of this — "
        f"{parsed.body}\n\n"
        "Anyway, nothing to read too much into. Talk soon."
    )
    recipients = parsed.recipients or (_recipient_for(registry, owner.id),)
    return [
        _write_artifact(
            artifact_id=f"{artifact.id}~dil",
            owner=owner,
            device_id=artifact.device_id,
            recipients=recipients,
            subject=f"Re: {parsed.subject}",
            body=softened,
            sent_at=artifact.acquisition_time,
            bound_proposition_ids=artifact.bound_proposition_ids,
            signal_weight=artifact.signal_weight * DILUTE_FACTOR,
            disclaimer=disclaimer,
        )
    ]


def split(
    artifact: Artifact,
    registry: PersonaRegistry,
    *,
    disclaimer: str,
    fragments: int = SPLIT_FRAGMENTS,
) -> list[Artifact]:
    """Fan the artifact out into >= 2 owner-distinct fragments.

    The original owner is kept as the first fragment so the bound proposition
    never loses an existing corroborator; remaining fragments are assigned to
    other personas. Combined fragment weight equals the original weight.
    """
    parsed = _parse_email(artifact.payload)
    original_owner = registry.get_persona(artifact.owner_id)
    others = [p for p in registry.personas() if p.id != original_owner.id]
    owners: list[Persona] = [original_owner, *others]
    n = min(fragments, len(owners))
    if n < 2:
        # Not enough distinct owners to split; fall back to diluting in place.
        return dilute(artifact, registry, disclaimer=disclaimer)

    chunks = _chunk_body(parsed.body, n)
    per_weight = artifact.signal_weight / n
    out: list[Artifact] = []
    for i in range(n):
        owner = owners[i]
        device = registry.devices_for(owner.id)[0]
        out.append(
            _write_artifact(
                artifact_id=f"{artifact.id}~frg{i}",
                owner=owner,
                device_id=device.id,
                recipients=(_recipient_for(registry, owner.id),),
                subject=f"{parsed.subject} (part {i + 1}/{n})",
                body=chunks[i],
                sent_at=artifact.acquisition_time,
                bound_proposition_ids=artifact.bound_proposition_ids,
                signal_weight=per_weight,
                disclaimer=disclaimer,
            )
        )
    return out


def _chunk_body(body: str, n: int) -> list[str]:
    words = body.split()
    if not words:
        return [f"(fragment {i + 1} of {n})" for i in range(n)]
    size = max(1, (len(words) + n - 1) // n)
    chunks = [" ".join(words[i * size : (i + 1) * size]) for i in range(n)]
    # Guarantee n non-empty chunks even when the body is shorter than n words.
    return [c if c else f"(fragment {i + 1} of {n})" for i, c in enumerate(chunks)]


def redact_relocate(
    artifact: Artifact, registry: PersonaRegistry, *, disclaimer: str
) -> list[Artifact]:
    """Remove the damning span and re-emit it elsewhere as a weaker corroborator.

    The flagged artifact's body (which carries the damning span) is dropped
    entirely and replaced with a mundane, non-dispositive note. The replacement
    is emitted on a *different* owner's device and bound to the same
    proposition(s), so the original owner no longer holds a smoking gun while
    corroboration is preserved (weakly) on a new custodian.
    """
    parsed = _parse_email(artifact.payload)
    new_owner, new_device_id = _email_persona_and_device(registry, exclude_id=artifact.owner_id)
    relocated_body = (
        "Following up on the earlier matter for the record. I can corroborate "
        "the general timeline in broad strokes, but I did not personally witness "
        "anything decisive and cannot speak to the specifics."
    )
    weight = artifact.signal_weight * DILUTE_FACTOR
    return [
        _write_artifact(
            artifact_id=f"{artifact.id}~rdr",
            owner=new_owner,
            device_id=new_device_id,
            recipients=(_recipient_for(registry, new_owner.id),),
            subject=f"Fwd: {parsed.subject}",
            body=relocated_body,
            sent_at=artifact.acquisition_time,
            bound_proposition_ids=artifact.bound_proposition_ids,
            signal_weight=weight,
            disclaimer=disclaimer,
        )
    ]


@dataclass(frozen=True)
class DemoteResult:
    """Output of the ``demote`` transform.

    - ``red_herring_support`` is the mutated artifact whose claim is now subtly
      false / misattributed; it is registered against ``false_proposition``.
    - ``true_signal`` re-emits the genuine signal weakly on a different owner so
      the original true proposition keeps a corroborator.
    - ``false_proposition`` is the contradicted lead; the Designer schedules a
      breaker bundle to refute it.
    """

    red_herring_support: Artifact
    true_signal: Artifact
    false_proposition: Proposition


def demote(
    artifact: Artifact,
    registry: PersonaRegistry,
    *,
    disclaimer: str,
    false_proposition_id: str | None = None,
    support_weight: float = DILUTE_FACTOR,
) -> DemoteResult:
    """Demote a flagged artifact to a refuted red herring.

    The artifact is mutated so its damning claim becomes a misattributed (and
    therefore false) lead, bound to a freshly minted false proposition. The
    genuine signal is preserved weakly on a different owner's device.
    """
    parsed = _parse_email(artifact.payload)
    owner = registry.get_persona(artifact.owner_id)
    false_prop_id = false_proposition_id or false_proposition_id_for(artifact)

    misattributed_body = (
        "On reflection I think I had this muddled — it now looks like it was "
        "someone else entirely, and on a different day. Disregard my earlier "
        "read of it; I clearly mixed up the dates and the people involved."
    )
    support = _write_artifact(
        artifact_id=f"{artifact.id}~dem",
        owner=owner,
        device_id=artifact.device_id,
        recipients=parsed.recipients or (_recipient_for(registry, owner.id),),
        subject=f"Re: {parsed.subject} (correction)",
        body=misattributed_body,
        sent_at=artifact.acquisition_time,
        bound_proposition_ids=(false_prop_id,),
        signal_weight=min(artifact.signal_weight, 1.0) * support_weight,
        disclaimer=disclaimer,
    )

    true_owner, true_device_id = _email_persona_and_device(registry, exclude_id=artifact.owner_id)
    true_signal = _write_artifact(
        artifact_id=f"{artifact.id}~true",
        owner=true_owner,
        device_id=true_device_id,
        recipients=(_recipient_for(registry, true_owner.id),),
        subject=f"Re: {parsed.subject}",
        body=(
            "For what it's worth I can quietly corroborate the original account; "
            "nothing dramatic on my end, just one more small data point."
        ),
        sent_at=artifact.acquisition_time,
        bound_proposition_ids=artifact.bound_proposition_ids,
        signal_weight=min(artifact.signal_weight, 1.0) * DILUTE_FACTOR / 2,
        disclaimer=disclaimer,
    )

    false_proposition = Proposition(
        id=false_prop_id,
        text=(
            "Contradicted lead: the case is best explained by a misattributed "
            f"actor/timeline suggested by artifact {artifact.id}."
        ),
    )
    return DemoteResult(
        red_herring_support=support,
        true_signal=true_signal,
        false_proposition=false_proposition,
    )


def remediate(
    artifact: Artifact,
    strategy: Strategy,
    registry: PersonaRegistry,
    *,
    disclaimer: str,
    fragments: int = SPLIT_FRAGMENTS,
) -> list[Artifact]:
    # These are email-shaped operations; all other profiles (pdf, xlsx_ledger,
    # jpeg, sms, system_log_csv) pass through unchanged.
    if artifact.profile != "email":
        return [artifact]
    if strategy == "split":
        return split(artifact, registry, disclaimer=disclaimer, fragments=fragments)
    if strategy == "dilute":
        return dilute(artifact, registry, disclaimer=disclaimer)
    if strategy == "redact_relocate":
        return redact_relocate(artifact, registry, disclaimer=disclaimer)
    if strategy == "demote":
        # demote has red-herring side effects and a richer return; the critique
        # pass calls demote() directly. Defuse here so a bare remediate() call
        # never silently leaves a smoking gun at full strength.
        result = demote(artifact, registry, disclaimer=disclaimer)
        return [result.red_herring_support, result.true_signal]
    raise ValueError(f"unknown remediation strategy: {strategy!r}")
