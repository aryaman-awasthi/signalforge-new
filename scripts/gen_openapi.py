"""Export the API document and regenerate the route catalogue in docs/11.

Two outputs, one source - the running application:

- `docs/11-openapi.json`: exactly what `GET /openapi.json` serves, including the
  `x-signalforge-auth` / `x-signalforge-permission` annotations read off each
  route's own guard (signalforge/api/openapi.py);
- the route catalogue between the generated markers in `docs/11-api-spec.md`,
  grouped by tag, naming the credential and the permission every route wants.

The catalogue is generated for the same reason the RTM and the ERD are: a route
table typed by hand is wrong the first time somebody adds a route, and "the
document says this needs manage_apps" is only worth reading if it was read out
of the guard rather than out of somebody's memory.

Usage: python scripts/gen_openapi.py [--check]
       --check exits non-zero instead of writing, for a pre-commit hook.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SPEC = ROOT / "docs" / "11-api-spec.md"
EXPORT = ROOT / "docs" / "11-openapi.json"

BEGIN = "<!-- BEGIN GENERATED ROUTE CATALOGUE (scripts/gen_openapi.py) -->"
END = "<!-- END GENERATED ROUTE CATALOGUE -->"

CREDENTIALS = {
    "session": "session",
    "ingest-key": "ingest key + HMAC",
    "none": "**none**",
}


def render_export(schema: dict) -> str:
    """Stable bytes: sorted keys, two-space indent, one trailing newline."""
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _permission_cell(guard) -> str:
    if not guard.permission:
        return "-" if guard.auth != "session" else "(session only)"
    scope = {"path": "path", "query": "`?app_id=`"}.get(guard.app_scope or "", "")
    return f"`{guard.permission}`" + (f" ({scope})" if scope else "")


def render_catalogue(app) -> str:
    from signalforge.api.openapi import TAGS, WEBSOCKET_METHOD, route_catalogue

    entries = route_catalogue(app)
    descriptions = {tag["name"]: tag["description"] for tag in TAGS}
    order = [tag["name"] for tag in TAGS]

    counts = {"session": 0, "ingest-key": 0, "none": 0}
    for entry in entries:
        counts[entry.guard.auth] += 1

    lines = [
        BEGIN,
        "",
        f"{len(entries)} routes: {counts['session']} need a human session, "
        f"{counts['ingest-key']} a sending application's ingest key, and "
        f"{counts['none']} no credential at all - and that last number is the one "
        "the security suite pins to a closed list.",
        "",
        "The permission column is read from each route's own RBAC dependency, so a "
        "route guarded by the wrong permission is documented as guarded by the "
        "wrong permission. It also says where a per-application role binding is "
        "evaluated from: `?app_id=`, the optional query parameter every route "
        "guarded by `deps.require` accepts, or `path`, the application named in "
        "the URL on the wizard's own routes.",
        "",
    ]

    groups = sorted(
        {entry.group for entry in entries},
        key=lambda name: (order.index(name) if name in order else len(order), name),
    )
    for group in groups:
        lines.append(f"### `{group}`")
        lines.append("")
        if group in descriptions:
            lines.append(descriptions[group])
            lines.append("")
        lines.append("| Method | Path | Credential | Permission | Purpose |")
        lines.append("|---|---|---|---|---|")
        for entry in (e for e in entries if e.group == group):
            method = entry.method
            if method == WEBSOCKET_METHOD:
                method = "WS"
            purpose = entry.purpose.replace("|", "\\|")
            lines.append(
                f"| {method} | `{entry.path}` | {CREDENTIALS[entry.guard.auth]} "
                f"| {_permission_cell(entry.guard)} | {purpose} |"
            )
        lines.append("")

    lines.append(END)
    return "\n".join(lines)


def splice(document: str, catalogue: str) -> str:
    if BEGIN not in document or END not in document:
        raise SystemExit(
            f"gen_openapi: {SPEC.name} is missing the generated markers "
            f"{BEGIN!r} / {END!r}"
        )
    head = document.split(BEGIN)[0]
    tail = document.split(END, 1)[1]
    return head + catalogue + tail


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the committed artifacts are stale instead of rewriting them",
    )
    args = parser.parse_args()

    from signalforge.api.main import app
    from signalforge.api.openapi import build_openapi

    schema = build_openapi(app, use_cache=False)
    export = render_export(schema)
    document = splice(SPEC.read_text(encoding="utf-8"), render_catalogue(app))

    stale = []
    if not EXPORT.is_file() or EXPORT.read_text(encoding="utf-8") != export:
        stale.append(EXPORT)
    if SPEC.read_text(encoding="utf-8") != document:
        stale.append(SPEC)

    if args.check:
        for path in stale:
            print(f"gen_openapi: {path.relative_to(ROOT)} is stale")
        print("gen_openapi: up to date" if not stale else "gen_openapi: run it")
        return 1 if stale else 0

    EXPORT.write_text(export, encoding="utf-8")
    SPEC.write_text(document, encoding="utf-8")
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(
        f"gen_openapi: wrote {EXPORT.relative_to(ROOT)} "
        f"({len(schema['paths'])} paths) and the route catalogue in "
        f"{SPEC.relative_to(ROOT)} at {generated}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
