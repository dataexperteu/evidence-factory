from __future__ import annotations

from datetime import UTC, datetime

import pytest

from evidence_factory.models import (
    ArtifactProfile,
    CaseBible,
    Device,
    MasterTimeline,
    Persona,
)
from evidence_factory.registry import PersonaRegistry


@pytest.fixture()
def timeline() -> MasterTimeline:
    return MasterTimeline(
        start=datetime(2024, 1, 1, tzinfo=UTC),
        end=datetime(2024, 3, 1, tzinfo=UTC),
    )


@pytest.fixture()
def personas() -> list[Persona]:
    return [
        Persona(
            name="alice",
            role="CFO",
            devices=[
                Device(
                    device_id="alice_laptop",
                    owner="alice",
                    permitted_profiles={
                        ArtifactProfile.EMAIL,
                        ArtifactProfile.PDF,
                        ArtifactProfile.XLSX,
                    },
                ),
                Device(
                    device_id="alice_phone",
                    owner="alice",
                    permitted_profiles={ArtifactProfile.SMS, ArtifactProfile.JPEG},
                ),
            ],
        ),
        Persona(
            name="bob",
            role="IT Admin",
            devices=[
                Device(
                    device_id="bob_workstation",
                    owner="bob",
                    permitted_profiles={
                        ArtifactProfile.SYSTEM_LOG_CSV,
                        ArtifactProfile.EMAIL,
                        ArtifactProfile.PDF,
                    },
                ),
                Device(
                    device_id="bob_phone",
                    owner="bob",
                    permitted_profiles={ArtifactProfile.SMS, ArtifactProfile.JPEG},
                ),
            ],
        ),
    ]


@pytest.fixture()
def registry(personas: list[Persona]) -> PersonaRegistry:
    return PersonaRegistry(personas)


@pytest.fixture()
def true_propositions() -> list[str]:
    return [
        "Alice transferred $50,000 to the Cayman Islands account on January 15, 2024",
        "Bob deleted the audit logs at 3:47 AM on January 16, 2024",
        "Charlie created a fake invoice for $25,000 to Meridian LLC",
    ]


@pytest.fixture()
def bible(
    personas: list[Persona], timeline: MasterTimeline, true_propositions: list[str]
) -> CaseBible:
    return CaseBible(
        personas=personas,
        master_timeline=timeline,
        taboo_topics=["Cayman Islands", "offshore account", "audit log deletion"],
        true_propositions=true_propositions,
        case_description="A corporate fraud investigation at Meridian Corp.",
    )
