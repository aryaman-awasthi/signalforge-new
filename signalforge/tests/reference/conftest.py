"""Fixtures for the reference-harness suite (S3-T06, REQ-038).

The harness is a Java application under reference-app/. These tests read its
sources and its published manifests directly, so the claim "this application
serves the frozen contract" is checked against what the application actually
declares rather than against a description of it. Where a running harness is
needed, the built jar is started for real - see test_reference_onboarding.py.

Nothing here imports anything from the harness, because nothing can: the only
coupling between the two sides is the wire format, which is the property under
test.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HARNESS = ROOT / "reference-app" / "paysphere"
CONFIG_REPO = ROOT / "paysphere-config"

JAVA = HARNESS / "src" / "main" / "java" / "com" / "novapay" / "paysphere"
RESOURCES = HARNESS / "src" / "main" / "resources"

ACTION_MANIFEST = RESOURCES / "observe" / "actions-manifest.json"
CHAOS_MANIFEST = RESOURCES / "observe" / "chaos-manifest.json"
SCHEMA_SQL = RESOURCES / "schema.sql"
DEPLOY_SCRIPT = CONFIG_REPO / "deploy.sh"

OBSERVE_CONTROLLER = JAVA / "observe" / "ObserveController.java"
ACTION_HANDLERS = JAVA / "actions" / "ActionHandlers.java"
CHAOS_ENGINE = JAVA / "chaos" / "ChaosEngine.java"
FABRIC = JAVA / "fabric" / "FabricSimulator.java"
API_KEY_FILTER = JAVA / "security" / "ApiKeyFilter.java"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_json(path: Path) -> dict:
    return json.loads(read(path))


def action_manifest() -> dict:
    return load_json(ACTION_MANIFEST)


def chaos_manifest() -> dict:
    return load_json(CHAOS_MANIFEST)


def mapped_paths(source: str) -> set[str]:
    """Every path literal the controller maps, whatever the HTTP method."""
    return set(re.findall(r'@(?:Get|Post|Put|Delete)Mapping\("([^"]+)"\)', source))


def registered_ids(source: str, call: str) -> set[str]:
    """The string keys of a `<map>.put("id", ...)` registry, across line breaks."""
    return set(re.findall(re.escape(call) + r'\(\s*"([a-z0-9][a-z0-9_-]*)"', source))
