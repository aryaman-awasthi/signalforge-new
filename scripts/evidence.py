"""Live traceability for one requirement (REQ-006, artifact 20).

    make evidence REQ=REQ-062
    python scripts/evidence.py REQ-062 --json

Answers, for a single REQ id, the four questions the guide's traceability
challenge asks - and answers the last one from the *running* platform rather
than from a document:

    1. what does this requirement say?          specs/requirements.yaml
    2. where is it implemented?                 files whose source names the id
    3. which tests prove it, and did they pass? the REQ markers, and the last run
    4. what has it actually done in production? events on the bus, right now

Question 3 comes from var/rtm/test-results.json - the same recording the RTM is
built from - so this and the RTM can never disagree.

Question 4 is derived rather than configured, and it is derived from this
repository's own layering rule. A requirement is implemented across a *feature
package* - `domain/detection`, `application/detection`, and so on - and that
package's application layer is where its event topics are declared. So: find the
files that name the requirement, take the feature packages they sit in, and read
the topic constants those packages define. Nothing is hand-mapped, so a
requirement whose implementation stops publishing a topic stops showing evidence
for it, which is the correct and uncomfortable behaviour.

Reads the event store directly and read-only. It never authenticates and never
writes, because it is a developer's traceability tool and not a route.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable

import yaml

ROOT = Path(__file__).resolve().parents[1]
SPECS = ROOT / "specs" / "requirements.yaml"
RESULTS = ROOT / "var" / "rtm" / "test-results.json"
DEFAULT_DB = ROOT / "var" / "signalforge.db"

SOURCE_ROOTS = ("signalforge", "web")
SKIP_DIRS = {"__pycache__", "node_modules", ".next", "tests"}
TOPIC_LITERAL = re.compile(r'"([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)"')
MAX_EVENTS = 20


def load_requirement(req_id: str) -> dict[str, Any]:
    document = yaml.safe_load(SPECS.read_text(encoding="utf-8"))
    for entry in document["requirements"]:
        if entry["id"] == req_id:
            return entry
    raise SystemExit(
        f"evidence: {req_id} is not in specs/requirements.yaml. "
        f"Ids are frozen (CLAUDE.md rule 3) - check the spelling."
    )


def implementation_files(req_id: str) -> list[Path]:
    """Source that names the requirement. Tests are excluded: they answer q3."""
    found: list[Path] = []
    for root in SOURCE_ROOTS:
        base = ROOT / root
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_dir() or any(part in SKIP_DIRS for part in path.parts):
                continue
            if path.suffix not in {".py", ".ts", ".tsx", ".yaml", ".yml"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if req_id in text:
                found.append(path)
    return sorted(found)


LAYERS = ("application", "domain", "workers", "infrastructure")


def feature_packages(paths: Iterable[Path]) -> list[str]:
    """The feature packages a requirement's implementation sits in.

    `signalforge/domain/detection/engine.py` -> `detection`. Derived from the
    layering CLAUDE.md fixes, so it is a rule of this repository rather than a
    table somebody maintains.
    """
    features: set[str] = set()
    for path in paths:
        parts = path.relative_to(ROOT).parts
        if len(parts) >= 4 and parts[0] == "signalforge" and parts[1] in LAYERS:
            features.add(parts[2])
    return sorted(features)


def topics_published_by(paths: Iterable[Path]) -> list[str]:
    """Dot-namespaced topic literals declared by the requirement's feature packages.

    Only literals assigned to a *_TOPIC constant or passed as `topic=` are taken,
    because a docstring mentioning `incident.declared` is prose, not a
    publication.
    """
    paths = list(paths)
    scan: set[Path] = {p for p in paths if p.suffix == ".py"}
    for feature in feature_packages(paths):
        for layer in LAYERS:
            package = ROOT / "signalforge" / layer / feature
            if package.is_dir():
                scan.update(package.glob("*.py"))
    topics: set[str] = set()
    for path in sorted(scan):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if "TOPIC" not in stripped and "topic=" not in stripped:
                continue
            topics.update(TOPIC_LITERAL.findall(stripped))
    return sorted(topics)


def tests_for(req_id: str) -> list[tuple[str, str]]:
    if not RESULTS.is_file():
        return []
    payload = json.loads(RESULTS.read_text(encoding="utf-8"))
    rows = []
    for nodeid, entry in sorted(payload["tests"].items()):
        if req_id in entry.get("reqs", []):
            rows.append((nodeid.replace("\\", "/"), entry.get("outcome") or "not-run"))
    return rows


def runtime_events(topics: list[str], db_path: Path) -> tuple[list[dict], str]:
    if not topics:
        return [], "this requirement's implementation defines no event topics"
    if not db_path.is_file():
        return [], f"no event store at {db_path} - run bash run.sh and use the platform"
    placeholders = ",".join("?" for _ in topics)
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            f"select seq, topic, occurred_at, actor_type, actor_id, incident_id, envelope "
            f"from events where topic in ({placeholders}) order by seq desc limit ?",
            (*topics, MAX_EVENTS),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        return [], f"could not read the event store: {exc}"
    finally:
        connection.close()
    events = [
        {
            "seq": seq,
            "topic": topic,
            "occurred_at": occurred_at,
            "actor": f"{actor_type}:{actor_id}",
            "incident_id": incident_id,
            "payload_keys": sorted(json.loads(envelope or "{}").get("payload") or {}),
        }
        for seq, topic, occurred_at, actor_type, actor_id, incident_id, envelope in rows
    ]
    if not events:
        return [], (
            "the topics exist but nothing has published one yet - exercise the "
            "feature (bash scripts/demo.sh) and run this again"
        )
    return events, ""


def build(req_id: str, db_path: Path) -> dict[str, Any]:
    requirement = load_requirement(req_id)
    files = implementation_files(req_id)
    topics = topics_published_by(files)
    tests = tests_for(req_id)
    events, note = runtime_events(topics, db_path)
    outcomes = {outcome for _n, outcome in tests}
    return {
        "requirement": requirement,
        "implementation": [str(p.relative_to(ROOT)).replace("\\", "/") for p in files],
        "topics": topics,
        "tests": [{"nodeid": n, "outcome": o} for n, o in tests],
        "verification": (
            "no automated test carries this REQ marker"
            if not tests
            else "failed"
            if "failed" in outcomes
            else "not run in the last recorded pass"
            if outcomes == {"not-run"}
            else "passed"
        ),
        "runtime_events": events,
        "runtime_note": note,
        "event_store": str(db_path),
    }


def render(report: dict[str, Any]) -> str:
    requirement = report["requirement"]
    out = [
        f"{requirement['id']}  {requirement['title']}",
        f"  area {requirement['area']} | sprint {requirement['sprint']} | "
        f"priority {requirement['priority']} | status {requirement['status']}",
        "",
        "  ACCEPTANCE",
        *[f"    {line}" for line in requirement["acceptance"].strip().splitlines()],
        "",
        f"  IMPLEMENTATION ({len(report['implementation'])} file(s))",
    ]
    out += [f"    {path}" for path in report["implementation"][:25]] or [
        "    none - no source names this requirement"
    ]
    if len(report["implementation"]) > 25:
        out.append(f"    ... and {len(report['implementation']) - 25} more")

    out += ["", f"  TESTS ({len(report['tests'])}) - verification: {report['verification']}"]
    out += [
        f"    [{row['outcome']:>8}] {row['nodeid']}" for row in report["tests"][:25]
    ] or ["    none carry this REQ marker"]
    if len(report["tests"]) > 25:
        out.append(f"    ... and {len(report['tests']) - 25} more")

    out += ["", f"  TOPICS PUBLISHED ({len(report['topics'])})"]
    out += [f"    {topic}" for topic in report["topics"]] or ["    none"]

    out += ["", f"  RUNTIME EVIDENCE  (from {report['event_store']})"]
    if report["runtime_events"]:
        for event in report["runtime_events"]:
            out.append(
                f"    seq {event['seq']:>6}  {event['occurred_at']}  {event['topic']}"
            )
            out.append(
                f"             by {event['actor']}"
                + (f"  incident {event['incident_id']}" if event["incident_id"] else "")
            )
    else:
        out.append(f"    {report['runtime_note']}")
    out.append("")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("req", nargs="?", help="a REQ id, e.g. REQ-062")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--db",
        default=os.environ.get("SIGNALFORGE_EVENT_DB", str(DEFAULT_DB)),
        help="path to the event store (default: var/signalforge.db)",
    )
    args = parser.parse_args()
    if not args.req:
        print("usage: make evidence REQ=REQ-062", file=sys.stderr)
        return 2

    report = build(args.req.strip().upper(), Path(args.db))
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0 if report["verification"] in ("passed",) else 0


if __name__ == "__main__":
    raise SystemExit(main())
