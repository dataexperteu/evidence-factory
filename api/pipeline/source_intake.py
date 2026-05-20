"""Source Intake — paste-only path for slice 1.

Pure normalisation: trim, collapse runs of blank lines, decode if bytes were
handed in. URL fetch and upload paths land in later slices.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class SourceText:
    body: str
    char_count: int


_BLANK_RUN = re.compile(r"\n{3,}")
_TRAILING_WS = re.compile(r"[ \t]+\n")


def ingest_paste(raw: str | bytes) -> SourceText:
    """Normalise a pasted source story into a SourceText.

    Decodes utf-8 if bytes were supplied, applies NFC normalisation, strips
    trailing whitespace on each line, and collapses 3+ blank lines to 2.
    Raises ValueError on empty input — an empty paste cannot drive the
    pipeline and we want that to fail loudly at the boundary.
    """
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace")
    else:
        text = raw
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _TRAILING_WS.sub("\n", text)
    text = _BLANK_RUN.sub("\n\n", text)
    text = text.strip()
    if not text:
        raise ValueError("source paste was empty after normalisation")
    return SourceText(body=text, char_count=len(text))
