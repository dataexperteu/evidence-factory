"""SMS / chat-export profile.

Produces a plain-text chat transcript (.txt) that mimics a standard SMS
export format.  The file is self-contained: every message line carries the
sender's display name, timestamp, and text body so the artifact is
independently meaningful as a corpus document.

The synthetic-evidence disclaimer is stamped into the file header and footer
so chain-of-custody readers always see it without needing out-of-band metadata.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")


def _slug(s: str, max_len: int = 40) -> str:
    s = _SAFE.sub("-", s).strip("-")
    return (s[:max_len] or "chat").lower()


@dataclass(frozen=True)
class SmsBrief:
    """Content brief handed to the SMS profile writer."""

    sender_name: str
    sender_number: str
    recipient_name: str
    recipient_number: str
    body: str  # LLM-generated exchange text; may be multi-paragraph
    sent_at: datetime


@dataclass(frozen=True)
class WrittenSms:
    filename: str
    payload: bytes
    sha256: str


def write_sms(brief: SmsBrief, *, disclaimer: str) -> WrittenSms:
    """Serialise a SmsBrief to a plain-text SMS chat-export transcript.

    ``disclaimer`` is stamped into the header and footer so every artifact
    carries the synthetic-evidence label alongside the corpus manifest entry.

    The output is non-deterministic by design (random suffix) to satisfy the
    two-runs-differ invariant, but the sha256 field always reflects the actual
    payload bytes.
    """
    if brief.sent_at.tzinfo is None:
        raise ValueError("sent_at must be timezone-aware")

    ts_str = brief.sent_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    lines: list[str] = [
        "# SMS EXPORT — SYNTHETIC EVIDENCE",
        f"# {disclaimer}",
        f"# Conversation between {brief.sender_name} ({brief.sender_number})"
        f" and {brief.recipient_name} ({brief.recipient_number})",
        f"# Exported at: {ts_str}",
        "",
    ]

    # Parse body into alternating turns, attributing each paragraph alternately
    paragraphs = [p.strip() for p in brief.body.strip().split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [brief.body.strip() or "(no content)"]

    speakers = [
        (brief.sender_name, brief.sender_number),
        (brief.recipient_name, brief.recipient_number),
    ]
    for i, para in enumerate(paragraphs):
        name, number = speakers[i % 2]
        lines.append(f"[{ts_str}] {name} ({number}):")
        for text_line in para.splitlines():
            lines.append(f"  {text_line}")
        lines.append("")

    lines.append(f"# END OF EXPORT — {disclaimer}")

    content = "\n".join(lines) + "\n"
    payload = content.encode("utf-8")
    sha = hashlib.sha256(payload).hexdigest()

    suffix = secrets.token_hex(3)
    ts_file = brief.sent_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    filename = f"{ts_file}_sms_{_slug(brief.sender_name)}_{suffix}.txt"
    return WrittenSms(filename=filename, payload=payload, sha256=sha)
