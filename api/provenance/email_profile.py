"""Email .eml profile — only profile implemented in slice 1.

Uses Python's stdlib `email.message.EmailMessage` so headers are RFC822-valid
by construction. The Provenance Catalog test re-parses these with the
third-party `mail-parser` library to guarantee independent validity.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid


@dataclass(frozen=True)
class EmailBrief:
    """Content brief handed to the email profile writer."""

    sender_name: str
    sender_address: str
    recipients: tuple[tuple[str, str], ...]  # (display_name, address)
    subject: str
    body: str
    sent_at: datetime


@dataclass(frozen=True)
class WrittenEmail:
    filename: str
    payload: bytes
    sha256: str
    message_id: str


_SUBJ_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")


def _slug(s: str, max_len: int = 40) -> str:
    s = _SUBJ_SAFE.sub("-", s).strip("-")
    return (s[:max_len] or "message").lower()


def write_email(brief: EmailBrief, *, disclaimer: str) -> WrittenEmail:
    """Serialise an EmailBrief to RFC822 bytes.

    `disclaimer` is the synthetic-evidence disclaimer that the PRD requires
    stamped into per-artifact metadata. We attach it as an `X-Synthetic-Evidence`
    header so the chain-of-custody manifest and the file both carry it.
    """
    if not brief.recipients:
        raise ValueError("email must have at least one recipient")
    if brief.sent_at.tzinfo is None:
        raise ValueError("sent_at must be timezone-aware")

    msg = EmailMessage()
    msg["From"] = f"{brief.sender_name} <{brief.sender_address}>"
    msg["To"] = ", ".join(f"{name} <{addr}>" for name, addr in brief.recipients)
    msg["Subject"] = brief.subject
    msg["Date"] = format_datetime(brief.sent_at)
    msg_id = make_msgid(domain="evidence-factory.example")
    msg["Message-ID"] = msg_id
    msg["X-Synthetic-Evidence"] = disclaimer
    msg.set_content(brief.body)

    payload = bytes(msg)
    sha = hashlib.sha256(payload).hexdigest()

    # Filename is owner-agnostic (the Packager places it under
    # /corpus/<custodian>/<device>/...). Salt with a short random suffix so
    # two artifacts with the same subject don't collide.
    suffix = secrets.token_hex(3)
    ts = brief.sent_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    filename = f"{ts}_{_slug(brief.subject)}_{suffix}.eml"
    return WrittenEmail(filename=filename, payload=payload, sha256=sha, message_id=msg_id)
