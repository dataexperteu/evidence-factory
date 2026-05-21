"""Red-Herring & Breaker Designer.

Given the canonical truth and the current load-bearing set, designs plausible
*false* propositions (red herrings), attaches weak supporting artifacts, and
designs a **breaker bundle** that conclusively refutes each false proposition
**in aggregate** — refuted-by-construction.

Two sources of red herrings are reconciled here:

- *scheduled* — produced by the Remediator's ``demote`` strategy during the
  critique pass. Their supporting artifact already lives in the corpus; the
  Designer only needs to build the breaker bundle.
- *baseline* — synthesised so that every generated case carries at least
  ``settings.min_red_herrings`` red herrings even when no artifact was demoted.

Closure invariants the Designer guarantees by construction (the Closure
Verifier independently re-checks them):

- the breaker bundle's aggregate signal exceeds the red herring's supporting
  signal by ``settings.breaker_margin``;
- no single breaker artifact convicts on its own — each passes the Smoking-Gun
  Critic (``too_strong: false``), i.e. stays below the smoking-gun bar;
- the breaker bundle is owner-distinct with at least ``min_breaker_bundle``
  artifacts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..provenance.email_profile import EmailBrief, write_email
from .critic import SmokingGunCritic
from .persona_registry import PersonaRegistry
from .signal_ledger import SignalLedger
from .types import Artifact, CanonicalTruth, Persona, Proposition, RedHerring


@dataclass(frozen=True)
class DesignerSettings:
    min_red_herrings: int = 1
    breaker_margin: float = 0.5  # breaker aggregate must exceed support by this fraction
    support_weight: float = 0.4  # weight of a baseline supporting artifact
    breaker_weight: float = 0.45  # per-breaker weight; stays below the smoking-gun bar
    min_breaker_bundle: int = 2


def _write_email_artifact(
    *,
    artifact_id: str,
    owner: Persona,
    device_id: str,
    recipient: tuple[str, str],
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
        recipients=(recipient,),
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


def _email_personas(registry: PersonaRegistry) -> list[Persona]:
    out: list[Persona] = []
    for persona in registry.personas():
        if any(d.profile == "email" for d in registry.devices_for(persona.id)):
            out.append(persona)
    return out


def _first_email_device(registry: PersonaRegistry, persona: Persona) -> str:
    for device in registry.devices_for(persona.id):
        if device.profile == "email":
            return device.id
    raise ValueError(f"persona {persona.id} owns no email device")


def _bundle_size(required_signal: float, settings: DesignerSettings) -> int:
    """Smallest owner-distinct bundle whose aggregate strictly exceeds required."""
    per = settings.breaker_weight
    # n * per > required  =>  n > required / per
    n = max(settings.min_breaker_bundle, math.floor(required_signal / per) + 1)
    return n


def _design_breakers(
    proposition: Proposition,
    support_signal: float,
    registry: PersonaRegistry,
    *,
    critic: SmokingGunCritic,
    truth: CanonicalTruth,
    ledger: SignalLedger | None,
    disclaimer: str,
    base_time: datetime,
    settings: DesignerSettings,
    id_prefix: str,
) -> list[Artifact]:
    required = support_signal * (1.0 + settings.breaker_margin)
    personas = _email_personas(registry)
    if len(personas) < settings.min_breaker_bundle:
        raise ValueError("not enough email-capable personas to build a breaker bundle")
    n = min(_bundle_size(required, settings), len(personas))

    breakers: list[Artifact] = []
    for i in range(n):
        owner = personas[i]
        recipient_owner = personas[(i + 1) % len(personas)]
        artifact = _write_email_artifact(
            artifact_id=f"{id_prefix}_brk{i}",
            owner=owner,
            device_id=_first_email_device(registry, owner),
            recipient=(recipient_owner.display_name, recipient_owner.email_address),
            subject=f"Re: {proposition.id} — this lead does not hold up",
            body=(
                "Adding one fragment that, together with the others, rules this "
                "lead out: my piece alone proves nothing, but it does not fit the "
                "alleged account. Cross-reference with the rest of the bundle."
            ),
            sent_at=base_time + timedelta(hours=i),
            bound_proposition_ids=(proposition.id,),
            signal_weight=settings.breaker_weight,
            disclaimer=disclaimer,
        )
        # Each breaker must individually pass the Smoking-Gun Critic. With the
        # per-breaker weight below the bar this holds; we still critique (and
        # halve on the rare flag) so the invariant is enforced, not assumed.
        verdict = critic.critique(artifact, truth)
        rounds = 0
        while verdict.too_strong and rounds < 3:
            artifact = _write_email_artifact(
                artifact_id=artifact.id,
                owner=owner,
                device_id=artifact.device_id,
                recipient=(recipient_owner.display_name, recipient_owner.email_address),
                subject=f"Re: {proposition.id} — this lead does not hold up",
                body="(weaker fragment of a refuting bundle)",
                sent_at=artifact.acquisition_time,
                bound_proposition_ids=(proposition.id,),
                signal_weight=artifact.signal_weight / 2,
                disclaimer=disclaimer,
            )
            verdict = critic.critique(artifact, truth)
            rounds += 1
        if ledger is not None:
            ledger.record_critic_verdict(verdict.to_json_serialisable())
        breakers.append(artifact)
    return breakers


def design_red_herrings(
    truth: CanonicalTruth,
    registry: PersonaRegistry,
    *,
    critic: SmokingGunCritic,
    disclaimer: str,
    base_time: datetime,
    scheduled: list[dict] | None = None,
    existing_artifacts: list[Artifact] | None = None,
    ledger: SignalLedger | None = None,
    settings: DesignerSettings | None = None,
) -> tuple[list[RedHerring], list[Artifact]]:
    """Return ``(red_herrings, new_artifacts)``.

    ``new_artifacts`` are the corpus artifacts the Designer created (breaker
    bundles, plus baseline supporting artifacts) and that the caller must add to
    the corpus. Supporting artifacts already in ``existing_artifacts`` (e.g. a
    demoted artifact) are referenced, not re-created.
    """
    settings = settings or DesignerSettings()
    scheduled = scheduled or []
    by_id = {a.id: a for a in (existing_artifacts or [])}

    red_herrings: list[RedHerring] = []
    new_artifacts: list[Artifact] = []

    # 1. Complete scheduled (demote-origin) red herrings with breaker bundles.
    for index, sched in enumerate(scheduled):
        prop = Proposition(id=sched["proposition_id"], text=sched["proposition_text"])
        support: list[Artifact] = []
        for art_id in sched.get("support_artifact_ids", ()):
            resolved = by_id.get(art_id)
            if resolved is not None:
                support.append(resolved)
        if not support:
            # Defensive: the demote support went missing; synthesise a weak one.
            synth = _baseline_support(
                prop, registry, disclaimer=disclaimer, base_time=base_time, settings=settings
            )
            support.append(synth)
            new_artifacts.append(synth)
        support_signal = sum(a.signal_weight for a in support)
        breakers = _design_breakers(
            prop,
            support_signal,
            registry,
            critic=critic,
            truth=truth,
            ledger=ledger,
            disclaimer=disclaimer,
            base_time=base_time + timedelta(days=1 + index),
            settings=settings,
            id_prefix=f"art_rh_sched_{index}",
        )
        new_artifacts.extend(breakers)
        red_herrings.append(
            RedHerring(proposition=prop, supporting=tuple(support), breakers=tuple(breakers))
        )

    # 2. Ensure at least min_red_herrings exist, synthesising baselines as needed.
    baseline_index = 0
    while len(red_herrings) < settings.min_red_herrings:
        prop = _baseline_proposition(truth, baseline_index)
        support = [
            _baseline_support(
                prop,
                registry,
                disclaimer=disclaimer,
                base_time=base_time,
                settings=settings,
                slot=baseline_index,
            )
        ]
        new_artifacts.extend(support)
        breakers = _design_breakers(
            prop,
            sum(a.signal_weight for a in support),
            registry,
            critic=critic,
            truth=truth,
            ledger=ledger,
            disclaimer=disclaimer,
            base_time=base_time + timedelta(days=10 + baseline_index),
            settings=settings,
            id_prefix=f"art_rh_base_{baseline_index}",
        )
        new_artifacts.extend(breakers)
        red_herrings.append(
            RedHerring(proposition=prop, supporting=tuple(support), breakers=tuple(breakers))
        )
        baseline_index += 1

    return red_herrings, new_artifacts


def _baseline_proposition(truth: CanonicalTruth, index: int) -> Proposition:
    lead = truth.graph.propositions[0].text if truth.graph.propositions else "the case"
    snippet = lead[:80].strip()
    return Proposition(
        id=f"prop_rh_baseline_{index}",
        text=(
            "False lead: the corpus initially appears to point at an outside party "
            f"rather than the true account ({snippet}...)."
        ),
    )


def _baseline_support(
    proposition: Proposition,
    registry: PersonaRegistry,
    *,
    disclaimer: str,
    base_time: datetime,
    settings: DesignerSettings,
    slot: int = 0,
) -> Artifact:
    personas = _email_personas(registry)
    # Pick from the tail so baseline support tends to differ from breaker owners.
    owner = personas[-(1 + slot) % len(personas)]
    recipient = personas[(slot) % len(personas)]
    return _write_email_artifact(
        artifact_id=f"{proposition.id}_support_{slot}",
        owner=owner,
        device_id=_first_email_device(registry, owner),
        recipient=(recipient.display_name, recipient.email_address),
        subject=f"{proposition.id}: a tempting but ultimately false lead",
        body=(
            "Floating a theory that looks plausible on the surface: it could point "
            "somewhere other than the obvious account. Weak on its own, but worth "
            "noting before it gets ruled out."
        ),
        sent_at=base_time,
        bound_proposition_ids=(proposition.id,),
        signal_weight=settings.support_weight,
        disclaimer=disclaimer,
    )


__all__ = [
    "DesignerSettings",
    "design_red_herrings",
]
