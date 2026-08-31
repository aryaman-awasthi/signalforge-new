"""S0-T03: specs integrity and RTM generation (REQ-006)."""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable

REQ_ID = re.compile(r"^REQ-\d{3}$")
CHECKLIST_AREAS = {
    "engineering-guide",
    "platform-identity",
    "event-backbone",
    "integrations",
    "detection-lifecycle",
    "war-room",
    "incident-brain",
    "governance-execution",
    "drills-audit",
}


def load_requirements():
    spec = yaml.safe_load((ROOT / "specs" / "requirements.yaml").read_text(encoding="utf-8"))
    return spec["requirements"]


@pytest.mark.req("REQ-006")
def test_requirements_ids_unique_and_well_formed():
    reqs = load_requirements()
    assert len(reqs) >= 40
    ids = [r["id"] for r in reqs]
    assert len(ids) == len(set(ids)), "duplicate REQ ids"
    for r in reqs:
        assert REQ_ID.match(r["id"]), r["id"]
        for field in ("title", "area", "sprint", "priority", "status", "acceptance"):
            assert r.get(field), f"{r['id']} missing {field}"
        assert r["status"] in {"planned", "partial", "built"}
        assert r["priority"] in {"must", "should"}
        assert re.match(r"^S[0-7]$", r["sprint"])


@pytest.mark.req("REQ-006")
def test_requirements_span_every_capability_area_and_sprint():
    reqs = load_requirements()
    assert {r["area"] for r in reqs} == CHECKLIST_AREAS
    assert {r["sprint"] for r in reqs} == {f"S{i}" for i in range(8)}


@pytest.mark.req("REQ-006")
def test_every_req_referenced_by_tests_exists_in_specs():
    known = {r["id"] for r in load_requirements()}
    referenced = set()
    marker = re.compile(r"pytest\.mark\.req\(\s*[\"'](REQ-\d{3})[\"']")
    for test_file in (ROOT / "signalforge" / "tests").glob("test_*.py"):
        referenced.update(marker.findall(test_file.read_text(encoding="utf-8")))
    unknown = referenced - known
    assert not unknown, f"tests reference REQ ids missing from specs: {sorted(unknown)}"


@pytest.mark.req("REQ-006")
def test_controls_have_five_categories_with_floor():
    spec = yaml.safe_load((ROOT / "specs" / "controls.yaml").read_text(encoding="utf-8"))
    categories = spec["categories"]
    assert set(categories) == {
        "allowed", "human_approved", "prohibited", "escalation", "auditability"
    }
    seen_ids = set()
    for name, cat in categories.items():
        assert cat["description"]
        assert cat["entries"], f"{name} has no entries"
        for entry in cat["entries"]:
            assert entry["id"] not in seen_ids, f"duplicate control id {entry['id']}"
            seen_ids.add(entry["id"])
            assert entry["statement"]
    floor = {e["id"] for c in categories.values() for e in c["entries"] if e.get("floor")}
    # the permanent policy floor: high-risk never auto-executes, audit never disabled
    assert "CTL-PRO-01" in floor and "CTL-PRO-03" in floor
    prohibited_ids = {e["id"] for e in categories["prohibited"]["entries"]}
    assert prohibited_ids <= floor, "every prohibited entry must be floor"


def run_gen_rtm(tmp_path: Path, results: Path | None):
    out = tmp_path / "rtm.md"
    cmd = [PYTHON, str(ROOT / "scripts" / "gen_rtm.py"), "--out", str(out)]
    cmd += ["--results", str(results if results else tmp_path / "missing.json")]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    return proc, out


@pytest.mark.req("REQ-006")
def test_rtm_generates_full_chain_with_zero_executed_tests(tmp_path):
    proc, out = run_gen_rtm(tmp_path, results=None)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    rtm = out.read_text(encoding="utf-8")
    assert "GENERATED" in rtm
    assert "executed: 0" in rtm
    for r in load_requirements():
        assert r["id"] in rtm, f"{r['id']} missing from RTM"


@pytest.mark.req("REQ-006")
def test_rtm_joins_recorded_outcomes_and_flags_unknown_reqs(tmp_path):
    results = tmp_path / "results.json"
    results.write_text(
        json.dumps(
            {
                "generated_at": "2026-09-01T00:00:00+00:00",
                "tests": {
                    "t.py::test_ok": {"reqs": ["REQ-001"], "outcome": "passed"},
                    "t.py::test_untagged": {"reqs": [], "outcome": "passed"},
                    "t.py::test_ghost": {"reqs": ["REQ-999"], "outcome": "failed"},
                },
            }
        ),
        encoding="utf-8",
    )
    proc, out = run_gen_rtm(tmp_path, results=results)
    assert proc.returncode == 2, "unknown REQ id must be an integrity error"
    rtm = out.read_text(encoding="utf-8")
    assert "REQ-999" in rtm and "test_ghost" in rtm
    assert "without REQ markers" in rtm and "test_untagged" in rtm
    assert "t.py::test_ok" in rtm
