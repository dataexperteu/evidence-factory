"""Packager — corpus layout, manifest SHA-256, SOLUTION separation, zip integrity."""

import csv
import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime

from api.pipeline.packager import (
    PackagerInput,
    assert_separation,
    build_zip,
    manifest_rows,
)
from api.pipeline.persona_registry import default_registry
from api.pipeline.signal_ledger import SignalLedger
from api.pipeline.types import (
    Artifact,
    CanonicalTruth,
    Proposition,
    PropositionGraph,
)


def _make_input() -> PackagerInput:
    registry = default_registry()
    truth = CanonicalTruth(
        outline="Holmes solved it.",
        graph=PropositionGraph(propositions=(Proposition(id="prop_1", text="Holmes was there."),)),
    )
    ledger = SignalLedger()
    artifacts: list[Artifact] = []
    for persona in registry.personas()[:2]:
        device = registry.devices_for(persona.id)[0]
        payload = f"From: {persona.email_address}\nSubject: t\n\nbody-{persona.id}".encode()
        sha = hashlib.sha256(payload).hexdigest()
        art = Artifact(
            id=f"art_{persona.id}",
            owner_id=persona.id,
            device_id=device.id,
            profile="email",
            filename=f"msg_{persona.id}.eml",
            payload=payload,
            sha256=sha,
            acquisition_time=datetime(2024, 6, 3, 9, 0, tzinfo=UTC),
            bound_proposition_ids=("prop_1",),
        )
        ledger.record(art)
        artifacts.append(art)
    return PackagerInput(
        truth=truth,
        artifacts=artifacts,
        ledger=ledger,
        registry=registry,
        attestation_text="Operator attested.",
        disclaimer="SYNTHETIC EVIDENCE — demo",
        run_id="run123",
    )


def test_zip_has_corpus_and_solution_top_level_dirs():
    zip_bytes = build_zip(_make_input())
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
    assert any(n.startswith("corpus/") for n in names)
    assert any(n.startswith("SOLUTION/") for n in names)


def test_solution_files_are_not_inside_corpus_tree():
    zip_bytes = build_zip(_make_input())
    assert_separation(zip_bytes)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            if name.startswith("corpus/"):
                assert "SOLUTION" not in name.upper()


def test_corpus_layout_is_custodian_then_device():
    zip_bytes = build_zip(_make_input())
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        artifact_paths = [
            n for n in zf.namelist() if n.startswith("corpus/") and n.endswith(".eml")
        ]
    for path in artifact_paths:
        parts = path.split("/")
        # corpus/<custodian>/<device>/<file>.eml
        assert len(parts) == 4, path
        assert parts[0] == "corpus"
        assert parts[3].endswith(".eml")


def test_manifest_sha256_matches_file_contents():
    zip_bytes = build_zip(_make_input())
    rows = manifest_rows(zip_bytes)
    assert rows, "manifest must have at least one row"
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for row in rows:
            with zf.open(row["path"]) as fh:
                content = fh.read()
            assert hashlib.sha256(content).hexdigest() == row["sha256"]
            assert {"acquisition_time", "owner", "device", "path", "sha256"} <= set(row.keys())


def test_solution_pack_contains_required_files():
    zip_bytes = build_zip(_make_input())
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = set(zf.namelist())
    for required in (
        "SOLUTION/truth_outline.md",
        "SOLUTION/proposition_graph.json",
        "SOLUTION/signal_ledger.json",
        "SOLUTION/per_artifact_provenance.json",
        "SOLUTION/remediation_log.json",
    ):
        assert required in names, f"missing {required}"


def test_remediation_log_default_is_empty_but_well_shaped():
    """If no remediation_log is passed, the packager still emits a
    structurally valid (empty) audit so downstream consumers can rely on
    the file shape unconditionally."""
    zip_bytes = build_zip(_make_input())
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        log = json.loads(zf.read("SOLUTION/remediation_log.json"))
    assert log["run_id"] == "run123"
    assert log["verdicts"] == []
    assert log["remediations"] == []


def test_remediation_log_passes_through_supplied_payload():
    base = _make_input()
    rl = {
        "verdicts": [
            {"artifact_id": "art_x", "too_strong": True, "reason": "r", "round": 0},
        ],
        "remediations": [
            {
                "original_artifact_id": "art_x",
                "strategy": "split",
                "new_artifact_ids": ["art_x_split_a", "art_x_split_b"],
                "rounds": 1,
                "final_too_strong": False,
                "initial_reason": "r",
                "final_reason": "ok",
            }
        ],
    }
    inp = PackagerInput(
        truth=base.truth,
        artifacts=base.artifacts,
        ledger=base.ledger,
        registry=base.registry,
        attestation_text=base.attestation_text,
        disclaimer=base.disclaimer,
        run_id=base.run_id,
        remediation_log=rl,
    )
    zip_bytes = build_zip(inp)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        log = json.loads(zf.read("SOLUTION/remediation_log.json"))
    assert log["verdicts"][0]["artifact_id"] == "art_x"
    assert log["remediations"][0]["strategy"] == "split"


def test_proposition_graph_json_is_valid_and_carries_run_id():
    zip_bytes = build_zip(_make_input())
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        data = json.loads(zf.read("SOLUTION/proposition_graph.json"))
    assert data["run_id"] == "run123"
    assert data["propositions"] == [{"id": "prop_1", "text": "Holmes was there."}]


def test_manifest_csv_round_trips():
    zip_bytes = build_zip(_make_input())
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        text = zf.read("corpus/manifest.csv").decode()
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    assert rows[0] == ["acquisition_time", "owner", "device", "path", "sha256"]
    assert len(rows) == 3  # header + 2 artifacts
