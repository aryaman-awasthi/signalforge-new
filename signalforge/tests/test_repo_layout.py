"""S0-T01: repo layout, build interface and app-agnostic guard are in place."""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

LAYOUT_DIRS = [
    "signalforge/contracts",
    "signalforge/domain",
    "signalforge/application",
    "signalforge/infrastructure",
    "signalforge/api",
    "signalforge/workers",
    "signalforge/seeds",
    "signalforge/tests",
    "web/app",
    "web/features",
    "web/lib",
    "web/design-system",
    "reference-app/paysphere",
    "paysphere-config",
    "specs",
    "drills",
    "seeds",
    "observability",
    "scripts",
    "docs",
]

MAKE_TARGETS = ["run:", "test:", "rtm:", "report:", "evidence:", "clean-check:"]


def run_check_clean() -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(ROOT / "scripts" / "check_clean.sh")],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )


@pytest.mark.req("REQ-000")
def test_repo_layout_exists():
    missing = [d for d in LAYOUT_DIRS if not (ROOT / d).is_dir()]
    assert not missing, f"missing layout directories: {missing}"


@pytest.mark.req("REQ-000")
def test_entry_scripts_exist():
    for name in ["run.sh", "reset.sh", "Makefile", "scripts/check_clean.sh"]:
        assert (ROOT / name).is_file(), f"missing {name}"


@pytest.mark.req("REQ-000")
def test_makefile_declares_all_targets():
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    missing = [t for t in MAKE_TARGETS if t not in makefile]
    assert not missing, f"Makefile missing targets: {missing}"


@pytest.mark.req("REQ-000")
def test_check_clean_passes_on_clean_tree():
    result = run_check_clean()
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.req("REQ-000")
def test_check_clean_catches_violation():
    probe = ROOT / ".tmp_check_clean_probe.txt"
    try:
        probe.write_text("paysphere leaked into the core\n", encoding="utf-8")
        result = run_check_clean()
        assert result.returncode == 1, "guard did not flag a planted violation"
        assert "FAILED" in result.stdout
    finally:
        probe.unlink(missing_ok=True)
