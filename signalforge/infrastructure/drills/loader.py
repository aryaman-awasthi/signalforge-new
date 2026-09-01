"""Scenario files on disk (REQ-070, REQ-071).

Drills are data under drills/ (CLAUDE.md rule 7). This reads one file and hands
back a validated Scenario; deleting the directory leaves a working product and
this loader with nothing to load, which is the intended failure.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import yaml

from signalforge.domain.drills.errors import ScenarioError
from signalforge.domain.drills.scenario import Scenario, load_scenario

DRILLS_DIR = Path(__file__).resolve().parents[3] / "drills"


def load_scenario_file(path: Path) -> Scenario:
    if not path.exists():
        raise ScenarioError(f"no such drill: {path}")
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ScenarioError(f"{path.name}: {exc}") from None
    return load_scenario(document)


def available_scenarios(directory: Path = DRILLS_DIR) -> Iterator[Path]:
    """Every drill file that declares itself a scenario.

    Sprint 4's acceptance drill lives in the same directory and is a different
    shape - a description of a fault for a live HTTP walkthrough rather than a
    stepped scenario - so files without ``steps:`` are skipped rather than
    refused.
    """
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.yaml")):
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue
        if isinstance(document, dict) and document.get("steps"):
            yield path
