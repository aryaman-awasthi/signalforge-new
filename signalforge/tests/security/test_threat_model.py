"""S1-T05: threat model artifact exists with STRIDE + AI rows (REQ-018)."""

from pathlib import Path

import pytest

DOC = Path(__file__).resolve().parents[3] / "docs" / "12-threat-model.md"


@pytest.mark.req("REQ-018")
def test_threat_model_covers_stride():
    text = DOC.read_text(encoding="utf-8")
    assert "STUB" not in text, "artifact 12 must be authored, not a stub"
    for category in [
        "Spoofing",
        "Tampering",
        "Repudiation",
        "Information disclosure",
        "Denial of service",
        "Elevation of privilege",
    ]:
        assert category in text, f"missing STRIDE category: {category}"


@pytest.mark.req("REQ-018")
def test_threat_model_covers_the_four_ai_rows():
    text = DOC.read_text(encoding="utf-8").lower()
    for row in [
        "prompt injection",
        "malicious document",
        "exfiltration",
        "tool invocation",
    ]:
        assert row in text, f"missing AI threat row: {row}"


@pytest.mark.req("REQ-018")
def test_threat_model_maps_mitigations_to_requirements():
    text = DOC.read_text(encoding="utf-8")
    for req in ["REQ-010", "REQ-012", "REQ-013", "REQ-015", "REQ-016", "REQ-017"]:
        assert req in text, f"mitigations must reference {req} for traceability"
