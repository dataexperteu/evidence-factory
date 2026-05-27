"""Profile cycling in emit_artifacts and noise generator — slice-24 acceptance criteria.

Verifies:
- emit_artifacts cycles through device.profiles deterministically per device
- A multi-profile device produces artifacts of multiple file extensions
- All 6 profiles appear across a generated corpus
- System-actor artifacts still appear
- Profile distribution across a multi-profile device is balanced
- Noise generator picks profiles randomly (all profiles covered over many runs)
"""

from __future__ import annotations

from datetime import UTC, datetime

from api.pipeline.artifact_emitter import emit_artifacts
from api.pipeline.llm_gateway import LLMGateway
from api.pipeline.noise_generator import NoiseGenerator
from api.pipeline.noise_guard import LeakContradictGuard
from api.pipeline.persona_registry import PersonaRegistry, default_registry
from api.pipeline.signal_ledger import SignalLedger
from api.pipeline.types import Device, Event, Persona

_TS = datetime(2024, 6, 3, 9, 0, tzinfo=UTC)


class _StubGateway(LLMGateway):
    def complete(self, role, prompt, *, cache_key=None):  # type: ignore[override]
        return "date,description,amount,category\n2024-06-01,stub,100.00,Misc"


def _event(idx: int, device_id: str, actor_id: str = "p_a") -> Event:
    return Event(
        id=f"ev_{idx:03d}",
        timestamp=_TS,
        actor_id=actor_id,
        device_id=device_id,
        summary=f"Event {idx}",
        proposition_ids=("prop_1",),
    )


# ---------------------------------------------------------------------------
# Cycling in emit_artifacts
# ---------------------------------------------------------------------------


def test_emitter_cycles_through_profiles():
    """Multiple events on the same multi-profile device cycle through its profiles."""
    p1 = Persona(id="p_a", display_name="Alice", email_address="alice@example.test")
    p2 = Persona(id="p_b", display_name="Bob", email_address="bob@example.test")
    # Device with two profiles: email then pdf
    d = Device(id="d_a_laptop", owner_id="p_a", label="a-laptop", profiles=("email", "pdf"))
    reg = PersonaRegistry([p1, p2], [d])
    events = [_event(i, "d_a_laptop") for i in range(6)]
    artifacts = emit_artifacts(
        events, reg, SignalLedger(), gateway=_StubGateway(), disclaimer="SYN"
    )
    profiles = [a.profile for a in artifacts]
    # Should cycle: email, pdf, email, pdf, email, pdf
    assert profiles == ["email", "pdf", "email", "pdf", "email", "pdf"]


def test_emitter_cycling_is_per_device():
    """Cycling counters are independent — two devices cycle independently."""
    p1 = Persona(id="p_a", display_name="Alice", email_address="alice@example.test")
    p2 = Persona(id="p_b", display_name="Bob", email_address="bob@example.test")
    da = Device(id="d_a", owner_id="p_a", label="a-laptop", profiles=("email", "pdf"))
    db = Device(id="d_b", owner_id="p_b", label="b-laptop", profiles=("pdf", "email"))
    reg = PersonaRegistry([p1, p2], [da, db])
    events = [
        _event(0, "d_a", actor_id="p_a"),  # a → email (counter 0)
        _event(1, "d_b", actor_id="p_b"),  # b → pdf (counter 0)
        _event(2, "d_a", actor_id="p_a"),  # a → pdf (counter 1)
        _event(3, "d_b", actor_id="p_b"),  # b → email (counter 1)
    ]
    artifacts = emit_artifacts(
        events, reg, SignalLedger(), gateway=_StubGateway(), disclaimer="SYN"
    )
    assert artifacts[0].profile == "email"
    assert artifacts[1].profile == "pdf"
    assert artifacts[2].profile == "pdf"
    assert artifacts[3].profile == "email"


def test_emitter_single_profile_device_unchanged():
    """A single-profile device still always emits that profile (cycling modulo 1)."""
    p1 = Persona(id="p_a", display_name="Alice", email_address="alice@example.test")
    p2 = Persona(id="p_b", display_name="Bob", email_address="bob@example.test")
    d = Device(id="d_a_phone", owner_id="p_a", label="a-phone", profiles=("sms",))
    reg = PersonaRegistry([p1, p2], [d])
    events = [_event(i, "d_a_phone") for i in range(4)]
    artifacts = emit_artifacts(
        events, reg, SignalLedger(), gateway=_StubGateway(), disclaimer="SYN"
    )
    assert all(a.profile == "sms" for a in artifacts)


# ---------------------------------------------------------------------------
# Mixed file extensions on the same device label (acceptance criterion 1)
# ---------------------------------------------------------------------------


def test_multi_profile_device_produces_multiple_extensions():
    """A multi-profile device produces artifacts with different file extensions."""
    p1 = Persona(id="p_a", display_name="Alice", email_address="alice@example.test")
    p2 = Persona(id="p_b", display_name="Bob", email_address="bob@example.test")
    d = Device(id="d_a_laptop", owner_id="p_a", label="holmes-laptop", profiles=("email", "pdf"))
    reg = PersonaRegistry([p1, p2], [d])
    # Emit 4 events → alternates email/pdf
    events = [_event(i, "d_a_laptop") for i in range(4)]
    artifacts = emit_artifacts(
        events, reg, SignalLedger(), gateway=_StubGateway(), disclaimer="SYN"
    )
    extensions = {a.filename.rsplit(".", 1)[-1] for a in artifacts}
    assert "eml" in extensions, "expected .eml from email profile"
    assert "pdf" in extensions, "expected .pdf from pdf profile"
    device_labels = {a.device_id for a in artifacts}
    assert device_labels == {"d_a_laptop"}, "all artifacts should come from same device"


# ---------------------------------------------------------------------------
# All 6 profiles across default-registry corpus (acceptance criterion 2)
# ---------------------------------------------------------------------------


def test_all_six_profiles_appear_in_default_registry_corpus():
    """Cycling across the default registry produces all 6 artifact profiles."""
    reg = default_registry()
    gateway = LLMGateway(mode="fixture", rng_seed=42)
    ledger = SignalLedger()
    # Build one event per device (13 persona devices + 2 system devices = 15)
    events: list[Event] = []
    persona_devices = {
        device.id: device for persona in reg.personas() for device in reg.devices_for(persona.id)
    }
    sys_devices = {d.id: d for d in reg.system_devices()}

    idx = 0
    for device_id, device in persona_devices.items():
        # Emit len(device.profiles) events per device to cycle through all profiles
        for _ in range(len(device.profiles)):
            events.append(
                Event(
                    id=f"ev_{idx:03d}",
                    timestamp=_TS,
                    actor_id=device.owner_id,
                    device_id=device_id,
                    summary="corpus coverage event",
                    proposition_ids=("prop_1",),
                )
            )
            idx += 1

    for device_id, device in sys_devices.items():
        events.append(
            Event(
                id=f"ev_{idx:03d}",
                timestamp=_TS,
                actor_id=device.owner_id,
                device_id=device_id,
                summary="system log event",
                proposition_ids=("prop_1",),
            )
        )
        idx += 1

    artifacts = emit_artifacts(events, reg, ledger, gateway=gateway, disclaimer="SYN")
    profiles_seen = {a.profile for a in artifacts}
    expected = {"email", "pdf", "sms", "jpeg", "xlsx_ledger", "system_log_csv"}
    assert profiles_seen == expected, f"missing profiles: {expected - profiles_seen}"


# ---------------------------------------------------------------------------
# System actor artifacts still appear (acceptance criterion 3)
# ---------------------------------------------------------------------------


def test_system_actor_artifacts_still_emitted():
    """System-actor devices (building-controller, phone-exchange) still produce artifacts."""
    reg = default_registry()
    gateway = LLMGateway(mode="fixture", rng_seed=99)
    ledger = SignalLedger()
    sys_events = [
        Event(
            id=f"ev_sys_{i}",
            timestamp=_TS,
            actor_id=d.owner_id,
            device_id=d.id,
            summary="system event",
            proposition_ids=("prop_1",),
        )
        for i, d in enumerate(reg.system_devices())
    ]
    artifacts = emit_artifacts(sys_events, reg, ledger, gateway=gateway, disclaimer="SYN")
    assert all(a.profile == "system_log_csv" for a in artifacts)
    # Both system actors produce artifacts
    actor_ids = {a.owner_id for a in artifacts}
    assert "sys_bldg_access" in actor_ids
    assert "sys_pbx" in actor_ids


# ---------------------------------------------------------------------------
# Balanced distribution (acceptance criterion 4)
# ---------------------------------------------------------------------------


def test_cycling_produces_balanced_distribution():
    """Profile distribution across a multi-profile device is balanced — no single profile dominates."""
    p1 = Persona(id="p_a", display_name="Alice", email_address="alice@example.test")
    p2 = Persona(id="p_b", display_name="Bob", email_address="bob@example.test")
    d = Device(
        id="d_a_ws", owner_id="p_a", label="a-workstation", profiles=("pdf", "email", "xlsx_ledger")
    )
    reg = PersonaRegistry([p1, p2], [d])
    # 9 events → 3 per profile (exactly balanced for a 3-profile tuple)
    events = [_event(i, "d_a_ws") for i in range(9)]
    artifacts = emit_artifacts(
        events, reg, SignalLedger(), gateway=_StubGateway(), disclaimer="SYN"
    )
    from collections import Counter

    dist = Counter(a.profile for a in artifacts)
    assert dist["pdf"] == 3
    assert dist["email"] == 3
    assert dist["xlsx_ledger"] == 3


# ---------------------------------------------------------------------------
# Noise generator — random selection across device profiles (acceptance criterion 2)
# ---------------------------------------------------------------------------


def test_noise_uses_profiles_from_device_profiles():
    """Noise artifacts only use profiles present in the device's profiles tuple."""
    gen = NoiseGenerator(
        LLMGateway(mode="fixture", rng_seed=5),
        default_registry(),
        LeakContradictGuard(LLMGateway(mode="fixture", rng_seed=5)),
    )
    artifacts, _ = gen.generate(
        propositions=["test proposition"],
        outline="test case",
        timeline_start=datetime(2024, 1, 1, tzinfo=UTC),
        timeline_end=datetime(2024, 3, 1, tzinfo=UTC),
        disclaimer="SYN",
        target_count=50,
    )
    reg = default_registry()
    for art in artifacts:
        device = reg.get_device(art.device_id)
        assert art.profile in device.profiles, (
            f"noise artifact {art.id} uses profile {art.profile!r} "
            f"not in device {device.id} profiles {device.profiles}"
        )


def test_noise_all_profiles_appear_across_large_corpus():
    """Noise generator produces all 6 profiles across a large corpus."""
    gen = NoiseGenerator(
        LLMGateway(mode="fixture", rng_seed=11),
        default_registry(),
        LeakContradictGuard(LLMGateway(mode="fixture", rng_seed=11)),
    )
    artifacts, summary = gen.generate(
        propositions=["test proposition"],
        outline="test case",
        timeline_start=datetime(2024, 1, 1, tzinfo=UTC),
        timeline_end=datetime(2024, 3, 1, tzinfo=UTC),
        disclaimer="SYN",
        target_count=400,
    )
    used = {a.profile for a in artifacts}
    expected = {"email", "pdf", "sms", "jpeg", "xlsx_ledger", "system_log_csv"}
    assert used == expected, f"missing profiles: {expected - used}"
