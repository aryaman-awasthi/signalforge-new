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


# --- starting the harness for real -------------------------------------------
#
# Two suites need a running JVM harness with different traffic settings - the
# onboarding acceptance wants the seeded generator running so a baseline has
# volume to learn from, and the detection acceptance wants it *off* so the test
# owns every payment and the numbers are exact rather than merely typical. The
# boot itself is the same, so it lives here rather than being copied.

import queue
import re as _re
import shutil
import subprocess
import threading
import time
from typing import Iterator, Optional

import httpx
import pytest

from signalforge.contracts.observe import OBSERVE_PATHS

JAR = HARNESS / "target" / "paysphere.jar"
BOOT_TIMEOUT_S = 90.0

READ_ONLY = "ps-ro-4f1c8a2b6d9e"
REMEDIATION = "ps-rem-7a3d5e0c1b84"
DRILL = "ps-drill-2c9f6b1a7e35"


def bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def _pump(stream, sink: queue.Queue) -> None:
    for line in stream:
        sink.put(line)
    sink.put(None)


def _clean_env() -> dict:
    import os

    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PAYSPHERE_", "SIGNALFORGE_"))
    }


def _await_ready(base_url: str, process, transcript, *, settle_s: float) -> None:
    """Ready means the observe surface answers - guarded, which is a real answer."""
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail("the harness exited during startup:\n" + "".join(transcript[-30:]))
        try:
            response = httpx.get(f"{base_url}{OBSERVE_PATHS['health']}", timeout=5.0)
        except httpx.HTTPError:
            time.sleep(0.5)
            continue
        if response.status_code in (200, 401):
            if settle_s:
                # Give the traffic generator a moment so the first scrape has
                # volume to baseline against; a baseline over an empty
                # application is not a baseline.
                time.sleep(settle_s)
            return
        time.sleep(0.5)
    pytest.fail("the harness never answered /observe/health")


def start_harness(
    workdir, *, env: Optional[dict] = None, settle_s: float = 2.0
) -> Iterator[str]:
    """The built harness on an ephemeral port with its own database.

    A generator, so a caller writes `yield from start_harness(...)` in its own
    fixture and the teardown stays here. Skips - loudly - when the jar has not
    been built, because a Java build is not something a Python suite performs.
    """
    if shutil.which("java") is None:
        pytest.skip("java is not on PATH; the reference harness cannot be started")
    if not JAR.exists():
        pytest.skip(
            f"{JAR.relative_to(HARNESS.parents[1])} is not built; "
            "run `bash reference-app/*/run.sh` or `mvn -DskipTests package` in the harness"
        )

    process = subprocess.Popen(
        ["java", "-jar", str(JAR)],
        cwd=str(HARNESS),
        env={
            **_clean_env(),
            "PAYSPHERE_PORT": "0",
            "PAYSPHERE_DATA_DIR": str(workdir / "data"),
            "PAYSPHERE_LOG_DIR": str(workdir / "logs"),
            **(env or {}),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    lines: queue.Queue = queue.Queue()
    threading.Thread(target=_pump, args=(process.stdout, lines), daemon=True).start()

    port = None
    transcript: list[str] = []
    deadline = time.monotonic() + BOOT_TIMEOUT_S
    while time.monotonic() < deadline and port is None:
        try:
            line = lines.get(timeout=1.0)
        except queue.Empty:
            if process.poll() is not None:
                break
            continue
        if line is None:
            break
        transcript.append(line)
        found = _re.search(r"Tomcat started on port (\d+)", line)
        if found:
            port = int(found.group(1))

    if port is None:
        process.kill()
        pytest.fail("the harness did not report a port:\n" + "".join(transcript[-30:]))

    base_url = f"http://127.0.0.1:{port}"
    _await_ready(base_url, process, transcript, settle_s=settle_s)
    try:
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
