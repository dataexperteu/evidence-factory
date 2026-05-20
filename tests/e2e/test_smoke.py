"""End-to-end smoke test: paste → corpus zip.

Runs the full pipeline on a small fixed public-domain source (Speckled Band
excerpt, Conan Doyle, 1892, public domain) and asserts:
- the zip has /corpus and /SOLUTION
- every manifest SHA-256 matches the file contents
- closure passes
- every artifact is owner-bound to a persona/device in the registry
- corpus contains both .eml and .pdf artifacts (slice-3 acceptance criterion)
- two runs with identical inputs produce different corpora
"""

import csv
import hashlib
import io
import json
import zipfile
from pathlib import Path

import mailparser
from pypdf import PdfReader

from api.pipeline.orchestrator import run_sync
from api.pipeline.persona_registry import default_registry_with_pdf

FIXTURE = Path(__file__).parent / "fixtures" / "speckled_band_excerpt.txt"


def _run() -> bytes:
    paste = FIXTURE.read_text(encoding="utf-8")
    events, result = run_sync(paste, attestation_checked=True)
    failed = [e for e in events if e.status == "failed"]
    assert not failed, f"pipeline emitted failure events: {failed}"
    assert result is not None, "pipeline did not produce a result"
    return result.zip_bytes


def test_smoke_zip_structure_and_separation():
    zip_bytes = _run()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
    assert any(n.startswith("corpus/") for n in names)
    assert any(n.startswith("SOLUTION/") for n in names)
    assert "corpus/manifest.csv" in names
    for required in (
        "SOLUTION/truth_outline.md",
        "SOLUTION/proposition_graph.json",
        "SOLUTION/signal_ledger.json",
        "SOLUTION/per_artifact_provenance.json",
    ):
        assert required in names
    for name in names:
        if name.startswith("corpus/"):
            assert "SOLUTION" not in name.upper()


def test_smoke_manifest_hashes_match_corpus_files():
    zip_bytes = _run()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        manifest_text = zf.read("corpus/manifest.csv").decode("utf-8")
        rows = list(csv.DictReader(io.StringIO(manifest_text)))
        assert rows
        for row in rows:
            with zf.open(row["path"]) as fh:
                actual = hashlib.sha256(fh.read()).hexdigest()
            assert actual == row["sha256"], f"hash mismatch for {row['path']}"


def test_smoke_artifacts_are_owner_bound_to_registry():
    """Every artifact must be emitted by a persona/device pair the
    Persona & Device Registry knows about."""
    zip_bytes = _run()
    registry = default_registry_with_pdf()
    valid_persona_slugs = {_slug(p.display_name): p.id for p in registry.personas()}
    valid_device_labels = {
        d.label: d.owner_id
        for d in (d for p in registry.personas() for d in registry.devices_for(p.id))
    }
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        artifact_paths = [
            n
            for n in zf.namelist()
            if n.startswith("corpus/") and (n.endswith(".eml") or n.endswith(".pdf"))
        ]
    assert artifact_paths
    for path in artifact_paths:
        _, custodian, device, _ = path.split("/")
        assert custodian in valid_persona_slugs, f"unknown custodian slug {custodian}"
        assert device in valid_device_labels, f"unknown device label {device}"
        owner_of_device = valid_device_labels[device]
        assert valid_persona_slugs[custodian] == owner_of_device, (
            f"device {device} not owned by {custodian}"
        )


def test_smoke_artifacts_are_rfc822_parseable_by_independent_library():
    zip_bytes = _run()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            if not (name.startswith("corpus/") and name.endswith(".eml")):
                continue
            payload = zf.read(name)
            parsed = mailparser.parse_from_bytes(payload)
            assert parsed.subject  # has a subject
            assert parsed.from_ and parsed.from_[0][1]  # has a From address
            assert parsed.to and parsed.to[0][1]  # has a To address


def test_smoke_closure_passes_for_every_proposition():
    """Belt-and-braces: cross-check that the signal ledger covers every
    proposition with the configured min_owner_distinct."""
    zip_bytes = _run()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        prop_graph = json.loads(zf.read("SOLUTION/proposition_graph.json"))
        ledger = json.loads(zf.read("SOLUTION/signal_ledger.json"))
    prop_ids = {p["id"] for p in prop_graph["propositions"]}
    owners_per_prop: dict[str, set[str]] = {pid: set() for pid in prop_ids}
    for entry in ledger["entries"]:
        for pid in entry["proposition_ids"]:
            owners_per_prop.setdefault(pid, set()).add(entry["owner_id"])
    for pid in prop_ids:
        assert len(owners_per_prop[pid]) >= 2, (
            f"proposition {pid} has only {len(owners_per_prop[pid])} owner-distinct corroborators"
        )


def test_smoke_corpus_contains_both_eml_and_pdf_artifacts():
    """Slice-3 acceptance criterion: the corpus must contain both .eml and .pdf files."""
    zip_bytes = _run()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
    eml_files = [n for n in names if n.startswith("corpus/") and n.endswith(".eml")]
    pdf_files = [n for n in names if n.startswith("corpus/") and n.endswith(".pdf")]
    assert eml_files, "corpus must contain at least one .eml artifact"
    assert pdf_files, "corpus must contain at least one .pdf artifact"


def test_smoke_pdf_artifacts_parseable_by_pypdf():
    """Every .pdf in the corpus must be structurally valid and carry correct metadata."""
    zip_bytes = _run()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        pdf_names = [n for n in zf.namelist() if n.startswith("corpus/") and n.endswith(".pdf")]
        assert pdf_names, "no PDF artifacts found"
        for name in pdf_names:
            payload = zf.read(name)
            reader = PdfReader(io.BytesIO(payload))
            assert len(reader.pages) >= 1
            meta = reader.metadata
            assert meta.get("/Author"), f"PDF {name} has no Author metadata"
            assert meta.get("/Title"), f"PDF {name} has no Title metadata"
            assert meta.get("/CreationDate"), f"PDF {name} has no CreationDate metadata"


def test_smoke_two_runs_produce_different_corpora():
    """Acceptance criterion: identical inputs yield a *different* corpus."""
    z1 = _run()
    z2 = _run()
    assert z1 != z2


def _slug(s: str) -> str:
    import re

    return re.sub(r"[^a-zA-Z0-9._-]+", "-", s).strip("-").lower() or "unknown"
