"""ArtifactWriter — structural protocol that all profile writers satisfy.

Each write_{profile}(brief, *, disclaimer) function satisfies this protocol:
it accepts a typed Brief and returns a WrittenArtifact with filename/payload/sha256.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class WrittenArtifact(Protocol):
    filename: str
    payload: bytes
    sha256: str
