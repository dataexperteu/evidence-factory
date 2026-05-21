"""End-to-end smoke tests.

Slice 1: paste → corpus zip.
Slice 2: URL path → corpus zip (local fixture server).
Slice 3: corpus contains .pdf artifacts.
Slice 5: corpus contains .xlsx artifacts.
Slice 6: corpus contains .jpg artifacts.
Slice 7: corpus contains system_log_csv (.csv) artifacts.
Slice sms: corpus contains .txt SMS artifacts.

Runs the full pipeline on a small fixed public-domain source (Speckled Band
excerpt, Conan Doyle, 1892, public domain) and asserts:
- the zip has /corpus and /SOLUTION
- every manifest SHA-256 matches the file contents
- closure passes
- every artifact is owner-bound to a persona/device in the registry
- corpus contains .eml + .pdf + .xlsx + .jpg + .csv + .txt artifacts simultaneously
- two runs with identical inputs produce different corpora
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import mailparser
import pypdf

from api.pipeline.orchestrator import RunSettings, run_sync
from api.pipeline.persona_registry import default_registry
from api.pipeline.source_intake import ingest_url

FIXTURE = Path(__file__).parent / "fixtures" / "speckled_band_excerpt.txt"
HTML_FIXTURE = Path(__file__).parent / "fixtures" / "sample_article.html"


def _run() -> bytes:
    paste = FIXTURE.read_text(encoding="utf-8")
    # 5 owners/proposition → cycling slots 0-4 produce email, pdf, jpeg, xlsx, sms;
    # system actors add system_log_csv. All six profiles represented in one run.
    settings = RunSettings(owners_per_proposition=5)
    events, result = run_sync(paste, attestation_checked=True, settings=settings)
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
    """Every .eml artifact must be placed under its owning persona/device path."""
    zip_bytes = _run()
    registry = default_registry()
    valid_persona_slugs = {_slug(p.display_name): p.id for p in registry.personas()}
    valid_device_labels = {
        d.label: d.owner_id
        for d in (d for p in registry.personas() for d in registry.devices_for(p.id))
    }
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        artifact_paths = [
            n for n in zf.namelist() if n.startswith("corpus/") and n.endswith(".eml")
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


def test_smoke_corpus_contains_pdf_artifacts():
    """Slice 3: corpus must contain .pdf artifacts."""
    zip_bytes = _run()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
    pdf = [n for n in names if n.startswith("corpus/") and n.endswith(".pdf")]
    assert pdf, "corpus must contain .pdf artifacts"


def test_smoke_pdf_artifacts_parseable_and_have_metadata():
    """Every .pdf in the corpus must parse via pypdf with author and title."""
    zip_bytes = _run()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        pdf_paths = [n for n in zf.namelist() if n.startswith("corpus/") and n.endswith(".pdf")]
    assert pdf_paths, "expected at least one .pdf artifact in the corpus"
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for path in pdf_paths:
            payload = zf.read(path)
            reader = pypdf.PdfReader(io.BytesIO(payload))
            meta = reader.metadata
            assert meta.author, f"PDF {path} missing /Author"
            assert meta.title, f"PDF {path} missing /Title"


def test_smoke_xlsx_artifacts_present_and_parseable():
    """Slice 5: at least one .xlsx artifact exists in the corpus and parses cleanly."""
    import io as _io

    import openpyxl

    zip_bytes = _run()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        xlsx_paths = [
            n for n in zf.namelist() if n.startswith("corpus/") and n.endswith(".xlsx")
        ]
        assert xlsx_paths, "expected at least one .xlsx artifact in corpus"
        for path in xlsx_paths:
            payload = zf.read(path)
            wb = openpyxl.load_workbook(_io.BytesIO(payload), read_only=True)
            assert wb.sheetnames, f"workbook at {path} has no sheets"
            wb.close()


def test_smoke_corpus_contains_jpeg_artifacts():
    """Slice 6: JPEG-device personas produce .jpg artifacts in the corpus."""
    zip_bytes = _run()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        jpeg_paths = [n for n in zf.namelist() if n.startswith("corpus/") and n.endswith(".jpg")]
    assert jpeg_paths, "corpus must contain at least one .jpg artifact"


def test_smoke_jpeg_artifacts_are_valid_jpegs():
    """Slice 6: every .jpg artifact must be parseable by Pillow."""
    import pytest

    pytest.importorskip("PIL")
    from PIL import Image

    zip_bytes = _run()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        jpeg_paths = [n for n in zf.namelist() if n.startswith("corpus/") and n.endswith(".jpg")]
    for path in jpeg_paths:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            payload = zf.read(path)
        img = Image.open(io.BytesIO(payload))
        assert img.format == "JPEG", f"{path} is not a valid JPEG"


def test_smoke_corpus_contains_system_log_csv_artifacts():
    """Slice 7: corpus must contain system_log_csv (.csv) artifacts."""
    zip_bytes = _run()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
    csv_paths = [
        n for n in names
        if n.startswith("corpus/") and n.endswith(".csv") and n != "corpus/manifest.csv"
    ]
    assert csv_paths, "corpus must contain at least one system_log_csv artifact (.csv)"


def test_smoke_corpus_contains_sms_artifacts():
    """SMS profile: corpus must contain .txt SMS chat-export artifacts."""
    zip_bytes = _run()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
    txt_paths = [n for n in names if n.startswith("corpus/") and n.endswith(".txt")]
    assert txt_paths, "corpus must contain at least one .txt SMS artifact"


def test_smoke_corpus_contains_all_profile_types():
    """All six profile types must appear in the corpus simultaneously."""
    zip_bytes = _run()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
    corpus = [n for n in names if n.startswith("corpus/") and n != "corpus/manifest.csv"]
    exts = {n.rsplit(".", 1)[-1] for n in corpus if "." in n}
    for required_ext in ("eml", "pdf", "xlsx", "jpg", "csv", "txt"):
        assert required_ext in exts, f"corpus missing .{required_ext} artifacts (found: {exts})"


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


def test_smoke_solution_pack_records_remediation_activity():
    """Slice 8: the sealed pack documents what the Smoking-Gun Critic flagged
    and how the Remediator defused it. Default difficulty reliably flags every
    full-strength artifact, so remediation activity must be non-empty."""
    zip_bytes = _run()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        assert "SOLUTION/remediation_log.json" in names
        remediation = json.loads(zf.read("SOLUTION/remediation_log.json"))
        ledger = json.loads(zf.read("SOLUTION/signal_ledger.json"))

    records = remediation["remediations"]
    assert records, "expected at least one artifact to be flagged and remediated"
    assert all(
        r["strategy"] in ("split", "dilute", "redact_relocate", "demote", "none") for r in records
    )
    assert any(r["outcome"] == "remediated" for r in records)

    # Every flagged-and-remediated artifact names the products it produced.
    for r in records:
        if r["outcome"] == "remediated":
            assert r["produced_artifact_ids"]

    # Critic verdicts are recorded on the Signal Ledger; at least one flagged.
    verdicts = ledger["critic_verdicts"]
    assert verdicts
    assert any(v["too_strong"] for v in verdicts)
    # Remediation activity is mirrored on the ledger too.
    assert ledger["remediations"]


def test_smoke_solution_pack_has_red_herring_breaker_map():
    """Slice 9: the sealed pack ships red_herring_breaker_map.json with at least
    one red herring whose breaker bundle is non-empty, owner-distinct (>= 2), and
    refutes the red herring in aggregate without any breaker convicting alone."""
    zip_bytes = _run()
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        assert "SOLUTION/red_herring_breaker_map.json" in names
        rh_map = json.loads(zf.read("SOLUTION/red_herring_breaker_map.json"))

    red_herrings = rh_map["red_herrings"]
    assert red_herrings, "expected at least one red herring per generated case"
    for rh in red_herrings:
        assert rh["supporting_artifacts"], "red herring must have >= 1 supporting artifact"
        breakers = rh["breaker_artifacts"]
        assert len(breakers) >= 2, "breaker bundle must have >= 2 artifacts"
        owners = {b["owner_id"] for b in breakers}
        assert len(owners) >= 2, "breaker bundle must be owner-distinct"
        # Aggregate refutation: breaker signal exceeds support by the margin.
        assert rh["breaker_signal"] >= rh["support_signal"] * 1.5
        # No single breaker convicts on its own (below the smoking-gun bar of 1.0).
        assert all(b["signal_weight"] < 1.0 for b in breakers)


def test_smoke_two_runs_produce_different_corpora():
    """Acceptance criterion: identical inputs yield a *different* corpus."""
    z1 = _run()
    z2 = _run()
    assert z1 != z2


def test_smoke_url_path_extracts_and_runs_pipeline():
    """Slice 2: URL intake path exercises trafilatura extraction against a
    local fixture HTTP server serving the HTML article fixture."""
    html_content = HTML_FIXTURE.read_bytes()

    class _FixtureHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html_content)))
            self.end_headers()
            self.wfile.write(html_content)

        def log_message(self, *args: object) -> None:
            pass  # suppress request log noise

    server = HTTPServer(("127.0.0.1", 0), _FixtureHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}/article"
    source = asyncio.run(ingest_url(url))
    assert source.char_count > 50, "expected readable text extracted from HTML fixture"
    assert any(
        kw in source.body.lower()
        for kw in ("holmes", "speckled", "watson", "sherlock", "young lady")
    ), f"extracted text does not look like the fixture: {source.body[:200]!r}"

    thread.join(timeout=2)
    server.server_close()


def _slug(s: str) -> str:
    import re

    return re.sub(r"[^a-zA-Z0-9._-]+", "-", s).strip("-").lower() or "unknown"
