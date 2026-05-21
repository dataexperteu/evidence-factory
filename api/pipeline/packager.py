"""Packager — assembles the final zip.

Layout:
  /corpus/<custodian-slug>/<device-label>/<filename>.eml
  /corpus/manifest.csv
  /SOLUTION/truth_outline.md
  /SOLUTION/proposition_graph.json
  /SOLUTION/signal_ledger.json
  /SOLUTION/per_artifact_provenance.json
  /SOLUTION/red_herring_breaker_map.json

Invariant: nothing whose source is the SOLUTION pack may appear under /corpus.
The zip writer enforces this by routing through `_corpus_member` /
`_solution_member`; tests assert no /SOLUTION file appears under /corpus.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime

from .persona_registry import PersonaRegistry
from .signal_ledger import SignalLedger
from .types import Artifact, CanonicalTruth, RedHerring

_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")


def _slug(s: str) -> str:
    return _SAFE.sub("-", s).strip("-").lower() or "unknown"


@dataclass(frozen=True)
class PackagerInput:
    truth: CanonicalTruth
    artifacts: list[Artifact]
    ledger: SignalLedger
    registry: PersonaRegistry
    attestation_text: str
    disclaimer: str
    run_id: str
    remediation_log: list[dict] = field(default_factory=list)
    red_herrings: list[RedHerring] = field(default_factory=list)


def _corpus_path(registry: PersonaRegistry, art: Artifact) -> str:
    actor_name = registry.get_actor_display_name(art.owner_id)
    device = registry.get_device(art.device_id)
    return f"corpus/{_slug(actor_name)}/{_slug(device.label)}/{art.filename}"


def build_zip(inp: PackagerInput) -> bytes:
    buf = io.BytesIO()
    manifest_rows: list[dict[str, str]] = []
    provenance: list[dict] = []
    seen_corpus_paths: set[str] = set()

    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        # corpus/ artifacts
        for art in inp.artifacts:
            path = _corpus_path(inp.registry, art)
            if path in seen_corpus_paths:
                raise ValueError(f"corpus path collision: {path}")
            seen_corpus_paths.add(path)
            zf.writestr(path, art.payload)
            verify = hashlib.sha256(art.payload).hexdigest()
            if verify != art.sha256:
                raise ValueError(
                    f"artifact {art.id} sha256 mismatch (recorded {art.sha256} vs actual {verify})"
                )
            manifest_rows.append(
                {
                    "acquisition_time": art.acquisition_time.isoformat(),
                    "owner": inp.registry.get_actor_display_name(art.owner_id),
                    "device": inp.registry.get_device(art.device_id).label,
                    "path": path,
                    "sha256": art.sha256,
                }
            )
            provenance.append(
                {
                    "artifact_id": art.id,
                    "path": path,
                    "owner_id": art.owner_id,
                    "device_id": art.device_id,
                    "profile": art.profile,
                    "proposition_ids": list(art.bound_proposition_ids),
                    "signal_weight": art.signal_weight,
                    "synthetic_evidence_disclaimer": inp.disclaimer,
                }
            )

        # corpus/manifest.csv
        csv_buf = io.StringIO()
        writer = csv.DictWriter(
            csv_buf,
            fieldnames=["acquisition_time", "owner", "device", "path", "sha256"],
            lineterminator="\n",
        )
        writer.writeheader()
        for row in manifest_rows:
            writer.writerow(row)
        zf.writestr("corpus/manifest.csv", csv_buf.getvalue())

        # corpus/MANIFEST.txt — plain-text synthetic-evidence disclaimer that a
        # reader sees without parsing any artifact's format-specific metadata.
        zf.writestr("corpus/MANIFEST.txt", _manifest_txt(inp.disclaimer))

        # SOLUTION/ pack
        zf.writestr(
            "SOLUTION/truth_outline.md",
            f"# Truth outline\n\n{inp.truth.outline}\n\n## Attestation\n\n{inp.attestation_text}\n",
        )
        zf.writestr(
            "SOLUTION/proposition_graph.json",
            json.dumps(
                {
                    "run_id": inp.run_id,
                    "propositions": [
                        {"id": p.id, "text": p.text} for p in inp.truth.graph.propositions
                    ],
                },
                indent=2,
            ),
        )
        zf.writestr(
            "SOLUTION/signal_ledger.json",
            json.dumps(
                {
                    "entries": inp.ledger.to_json_serialisable(),
                    "critic_verdicts": inp.ledger.critic_verdicts(),
                    "remediations": inp.ledger.remediations(),
                },
                indent=2,
            ),
        )
        zf.writestr(
            "SOLUTION/per_artifact_provenance.json",
            json.dumps({"run_id": inp.run_id, "artifacts": provenance}, indent=2),
        )
        zf.writestr(
            "SOLUTION/remediation_log.json",
            json.dumps({"run_id": inp.run_id, "remediations": inp.remediation_log}, indent=2),
        )
        zf.writestr(
            "SOLUTION/red_herring_breaker_map.json",
            json.dumps(
                {
                    "run_id": inp.run_id,
                    "red_herrings": [_red_herring_entry(rh) for rh in inp.red_herrings],
                },
                indent=2,
            ),
        )

    return buf.getvalue()


def _manifest_txt(disclaimer: str) -> str:
    return (
        "SYNTHETIC EVIDENCE\n"
        "==================\n\n"
        f"{disclaimer}\n\n"
        "Every artifact in this corpus is synthetic and carries the same "
        "disclaimer stamped into a format-appropriate metadata field.\n"
    )


def _artifact_ref(art: Artifact) -> dict:
    return {
        "artifact_id": art.id,
        "owner_id": art.owner_id,
        "device_id": art.device_id,
        "profile": art.profile,
        "signal_weight": art.signal_weight,
        "bound_proposition_ids": list(art.bound_proposition_ids),
    }


def _red_herring_entry(rh: RedHerring) -> dict:
    return {
        "proposition": {"id": rh.proposition.id, "text": rh.proposition.text},
        "supporting_artifacts": [_artifact_ref(a) for a in rh.supporting],
        "breaker_artifacts": [_artifact_ref(a) for a in rh.breakers],
        "support_signal": rh.support_signal,
        "breaker_signal": rh.breaker_signal,
        "breaker_owner_count": len(rh.distinct_breaker_owners()),
    }


def assert_separation(zip_bytes: bytes) -> None:
    """Belt-and-braces invariant: no SOLUTION file may appear under /corpus."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
    for name in names:
        if name.startswith("corpus/") and "SOLUTION" in name.upper():
            raise AssertionError(f"SOLUTION content leaked into corpus tree: {name}")


def manifest_rows(zip_bytes: bytes) -> list[dict[str, str]]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        with zf.open("corpus/manifest.csv") as fh:
            text = fh.read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


def utc_now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"
