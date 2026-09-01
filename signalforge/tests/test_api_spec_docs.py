"""S3-T07: artifact 11 is the API's own document (REQ-036, REQ-012, REQ-004).

An API specification is worth reading only if it cannot quietly stop being true.
Three claims are made in docs/11-api-spec.md and each one is pinned here:

- the exported `docs/11-openapi.json` is what the running application would serve
  today, not what it served when somebody last remembered to export it;
- the route catalogue lists **every** route with the credential and the
  permission read out of that route's own guard, so an undocumented route and a
  misdocumented permission both fail;
- docs/04 chapter 6 states onboarding as a contract, and the tests it cites as
  evidence exist.

The failure message on each is the command that fixes it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from scripts.gen_openapi import BEGIN, END, render_catalogue, render_export
from signalforge.api.main import app
from signalforge.api.openapi import TAGS, WEBSOCKET_METHOD, build_openapi, route_catalogue
from signalforge.contracts.ingest import (
    HDR_KEY_ID,
    HDR_SIGNATURE,
    HDR_TIMESTAMP,
    INGEST_PATHS,
)

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
SPEC = DOCS / "11-api-spec.md"
EXPORT = DOCS / "11-openapi.json"
FUNCTIONAL = DOCS / "04-functional-spec.md"

REGENERATE = "run `python scripts/gen_openapi.py` (make openapi)"


def spec_text() -> str:
    return SPEC.read_text(encoding="utf-8")


def catalogue_rows() -> dict[tuple[str, str], tuple[str, str]]:
    """(method, path) -> (credential cell, permission cell) from the doc table."""
    block = spec_text().split(BEGIN)[1].split(END)[0]
    rows: dict[tuple[str, str], tuple[str, str]] = {}
    for line in block.splitlines():
        if not line.startswith("| ") or line.startswith("| Method") or "|---" in line:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        rows[(cells[0], cells[1].strip("`"))] = (cells[2], cells[3])
    return rows


@pytest.mark.req("REQ-036", "REQ-004")
def test_the_export_is_what_the_application_would_serve_today():
    """The one property that makes an exported document worth publishing."""
    assert EXPORT.is_file(), f"docs/11-openapi.json is missing - {REGENERATE}"
    fresh = render_export(build_openapi(app, use_cache=False))
    assert EXPORT.read_text(encoding="utf-8") == fresh, (
        f"the exported OpenAPI document is stale - {REGENERATE}"
    )


@pytest.mark.req("REQ-036")
def test_the_running_server_serves_the_exported_document(api):
    """Not "generated from the same code" - the same document, over HTTP."""
    served = api.client.get("/openapi.json")
    assert served.status_code == 200
    assert served.json() == json.loads(EXPORT.read_text(encoding="utf-8"))


@pytest.mark.req("REQ-036")
def test_the_generator_is_reproducible():
    """Two runs must agree, or "the document changed" stops meaning "the API
    changed" and nobody reads the diff."""
    assert render_export(build_openapi(app, use_cache=False)) == render_export(
        build_openapi(app, use_cache=False)
    )
    assert render_catalogue(app) == render_catalogue(app)


@pytest.mark.req("REQ-012", "REQ-036")
def test_every_route_is_in_the_catalogue_with_the_permission_it_enforces():
    """A route missing from the catalogue is a surface nobody reviewed; a route
    listed with the wrong permission is worse, because it was reviewed wrongly."""
    documented = catalogue_rows()
    live = {
        (entry.method, entry.path): entry
        for entry in route_catalogue(app)
    }

    missing = sorted(f"{m} {p}" for (m, p) in live if (m, p) not in documented)
    extra = sorted(f"{m} {p}" for (m, p) in documented if (m, p) not in live)
    assert not missing, f"routes missing from the docs/11 catalogue: {missing} - {REGENERATE}"
    assert not extra, f"the catalogue lists routes the API does not serve: {extra}"

    wrong = []
    for key, entry in live.items():
        credential, permission = documented[key]
        if entry.guard.permission and f"`{entry.guard.permission}`" not in permission:
            wrong.append(f"{key} documented as {permission!r}")
        if entry.guard.auth == "none" and "none" not in credential:
            wrong.append(f"{key} needs no credential and the catalogue hides it")
    assert not wrong, f"catalogue disagrees with the guards: {wrong} - {REGENERATE}"


@pytest.mark.req("REQ-036")
def test_every_group_in_the_catalogue_is_described():
    """A tag with no description publishes a heading and no meaning."""
    described = {tag["name"] for tag in TAGS}
    groups = {entry.group for entry in route_catalogue(app)}
    assert not groups - described, f"undescribed API groups: {sorted(groups - described)}"


@pytest.mark.req("REQ-004", "REQ-032")
def test_the_frozen_ingest_paths_are_published_with_their_headers():
    """The contract freezes the paths and the headers; the published document has
    to say the same thing, or an integrator implements something else."""
    schema = json.loads(EXPORT.read_text(encoding="utf-8"))
    schemes = schema["components"]["securitySchemes"]
    assert schemes["ingestSignature"]["name"] == HDR_SIGNATURE
    assert schemes["ingestTimestamp"]["name"] == HDR_TIMESTAMP

    for stream, path in INGEST_PATHS.items():
        operation = schema["paths"][path]["post"]
        assert "202" in operation["responses"], f"{path} must document the 202 ack"
        assert operation["security"] == [
            {"ingestKey": [], "ingestSignature": [], "ingestTimestamp": []}
        ], f"{path} must require the token and the signature together"
        assert operation["x-signalforge-optional-headers"] == [HDR_KEY_ID]
        # A key scoped to another stream is a 403, so the stream is contract.
        assert operation["x-signalforge-ingest-stream"] == stream


@pytest.mark.req("REQ-036", "REQ-012")
def test_the_api_spec_is_authored_not_a_stub():
    text = spec_text()
    assert "STUB" not in text
    for section in [
        "## Conventions",
        "### Credentials",
        "### Per-application scope",
        "### Errors",
        "## The exported document",
        "## Route catalogue",
        "## Identity, org and on-call",
        "## Events, replay and the platform surface",
        "## Adapter registry and connectors",
        "## Onboarding: `/apps`",
        "## Ingest keys",
        "## Events emitted",
        "## `GET /ws`",
        "## What an onboarded application serves",
    ]:
        assert section in text, f"docs/11 is missing {section!r}"
    # The WebSocket cannot be in OpenAPI, so the document has to carry it.
    assert WEBSOCKET_METHOD == "WS" and "| WS | `/ws` |" in text
    for claim in ["x-signalforge-permission", "504", "make openapi"]:
        assert claim in text, f"docs/11 must explain {claim!r}"


@pytest.mark.req("REQ-036", "REQ-038")
def test_docs_04_states_onboarding_as_a_contract():
    text = FUNCTIONAL.read_text(encoding="utf-8")
    for section in [
        "## 3. Onboarding: the wizard",
        "## 4. The onboarding screens",
        "## 5. The reference harness",
        "## 6. Onboarding as a contract",
        "### 6.1 What an application must provide",
        "### 6.2 What SignalForge does not ask for",
        "### 6.3 The order, and why it is an order",
        "### 6.4 Two directions in",
        "### 6.5 Onboarding is re-entrant",
        "### 6.6 Where every onboarding failure lands",
        "### 6.7 Offboarding",
    ]:
        assert section in text, f"docs/04 is missing {section!r}"


@pytest.mark.req("REQ-036")
def test_the_failure_catalogue_in_docs_04_cites_tests_that_exist():
    """A failure table that names its evidence is only as good as the evidence.
    Every one of the seven mandated failure modes cites a file; if a file is
    renamed away, the claim becomes unverifiable and this fails."""
    chapter = FUNCTIONAL.read_text(encoding="utf-8").split("### 6.6")[1].split("### 6.7")[0]
    cited = set(re.findall(r"`(test_[a-z0-9_]+\.py)`", chapter))
    assert len(cited) >= 8, f"the failure catalogue must cite its evidence: {cited}"
    for name in sorted(cited):
        assert list((ROOT / "signalforge" / "tests").rglob(name)), (
            f"docs/04 cites {name}, which does not exist"
        )


@pytest.mark.req("REQ-036")
def test_the_readiness_contract_in_docs_04_matches_the_domain():
    """The onboarding contract publishes what each check is worth and which
    checks block. If the domain re-weights or unblocks one, the table stops
    being a contract and becomes a rumour."""
    from signalforge.domain.onboarding.readiness import ReadinessInputs, evaluate

    checks = evaluate(ReadinessInputs()).checks
    chapter = FUNCTIONAL.read_text(encoding="utf-8").split("### 6.1")[1].split("### 6.2")[0]

    points: list[int] = []
    blocking = 0
    for line in chapter.splitlines():
        if not line.startswith("| ") or "|---" in line or line.startswith("| What"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells[1].isdigit():
            points.append(int(cells[1]))
        if cells[2] == "**yes**":
            blocking += 1

    assert sorted(points) == sorted(check.weight for check in checks), (
        "docs/04 6.1 publishes readiness weights the domain does not use"
    )
    assert sum(points) == 100, "the published weights must still sum to 100"
    assert blocking == len([check for check in checks if check.blocking])
