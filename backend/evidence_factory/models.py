from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class ArtifactProfile(StrEnum):
    EMAIL = "email"
    SMS = "sms"
    SMS_CHAT_EXPORT = "sms_chat_export"
    PDF = "pdf"
    XLSX = "xlsx"
    JPEG = "jpeg"
    LOG = "log"


@dataclass
class Artifact:
    owner: str
    device: str
    profile: ArtifactProfile
    timestamp: datetime
    content: bytes
    text_content: str
    metadata: dict[str, Any]
    artifact_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    is_noise: bool = True


@dataclass
class MasterTimeline:
    start: datetime
    end: datetime

    def total_hours(self) -> float:
        delta = self.end - self.start
        return delta.total_seconds() / 3600


@dataclass
class Device:
    device_id: str
    owner: str
    permitted_profiles: set[ArtifactProfile]


@dataclass
class Persona:
    name: str
    devices: list[Device]
    role: str = ""
    background: str = ""


@dataclass
class CaseBible:
    personas: list[Persona]
    master_timeline: MasterTimeline
    taboo_topics: list[str]
    true_propositions: list[str]
    case_description: str = ""


@dataclass
class NoiseSummary:
    target_count: int
    generated_count: int
    rejected_count: int
    profile_distribution: dict[str, int]
    cache_hits: int
    cache_misses: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_count": self.target_count,
            "generated_count": self.generated_count,
            "rejected_count": self.rejected_count,
            "profile_distribution": self.profile_distribution,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
        }
