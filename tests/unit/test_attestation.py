"""Attestation Gate — records the attestation and sets the synthetic-evidence
disclaimer field on the case context."""

import pytest

from api.pipeline.attestation import (
    SYNTHETIC_EVIDENCE_DISCLAIMER,
    AttestationRequired,
    CaseContext,
    gate,
)


def test_gate_requires_attestation():
    with pytest.raises(AttestationRequired):
        gate(False)


def test_gate_returns_case_context_with_synthetic_evidence_flag():
    case = gate(True, operator_label="demo-op")
    assert isinstance(case, CaseContext)
    assert case.synthetic_evidence is True
    assert case.disclaimer == SYNTHETIC_EVIDENCE_DISCLAIMER
    assert case.attestation.checked is True
    assert case.attestation.operator_label == "demo-op"


def test_gate_records_attestation_text():
    case = gate(True, operator_label="demo-op")
    text = case.attestation.as_text()
    assert "demo-op" in text
    assert "attested authorised use" in text


def test_gate_disclaimer_is_overridable():
    case = gate(True, disclaimer="CUSTOM SYNTHETIC NOTICE")
    assert case.disclaimer == "CUSTOM SYNTHETIC NOTICE"
    assert case.synthetic_evidence is True
