"""S3-T04: the topology upload step is idempotent by content (REQ-036, REQ-031).

Written failing-first, because this is an idempotency rule (CLAUDE.md session
protocol). An operator who is not sure the upload worked will upload again, and
a wizard that answered "topology v2 published" to an identical document would
make "has the map changed since the incident started" unanswerable - which is
the one question blast radius depends on.

The rule: a document's version is its own when it declares one and a content
fingerprint when it does not, re-uploading the same content is *one* version and
*one* event, and a document that would import half a graph is refused whole.
"""

import copy

import pytest

from signalforge.tests.onboarding.conftest import (  # noqa: F401
    APP_ID,
    SIMPLE_TOPOLOGY,
    observe,
    owner,
    register_app,
    topics,
    upload_topology,
)


@pytest.mark.req("REQ-036")
def test_upload_versions_by_content_and_publishes_once(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)

    first = upload_topology(api, token, SIMPLE_TOPOLOGY)
    assert first["unchanged"] is False
    assert first["node_count"] == 2 and first["edge_count"] == 1
    assert first["version"].startswith("sha256:"), (
        "a document declaring no version is versioned by its content"
    )

    events = topics(api, token, "topology.updated")
    assert len(events) == 1

    listing = api.client.get(
        f"/apps/{APP_ID}/topology", headers=api.auth_header(token)
    ).json()
    assert listing["version"] == first["version"]
    assert len(listing["versions"]) == 1
    assert [node["node_id"] for node in listing["snapshot"]["nodes"]] == ["web", "db"]


@pytest.mark.req("REQ-036")
def test_reuploading_the_same_document_is_one_version_and_no_second_event(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)

    first = upload_topology(api, token, SIMPLE_TOPOLOGY)
    again = upload_topology(api, token, SIMPLE_TOPOLOGY)

    assert again["unchanged"] is True
    assert again["version"] == first["version"]
    assert len(topics(api, token, "topology.updated")) == 1

    listing = api.client.get(
        f"/apps/{APP_ID}/topology", headers=api.auth_header(token)
    ).json()
    assert len(listing["versions"]) == 1


@pytest.mark.req("REQ-036")
def test_node_order_and_capture_time_do_not_make_a_new_version(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)

    first = upload_topology(api, token, SIMPLE_TOPOLOGY)

    reordered = copy.deepcopy(SIMPLE_TOPOLOGY)
    reordered["nodes"].reverse()
    reordered["captured_at"] = "2026-09-02T09:00:00+00:00"
    second = upload_topology(api, token, reordered)

    assert second["version"] == first["version"]
    assert second["unchanged"] is True


@pytest.mark.req("REQ-036")
def test_a_changed_map_is_a_new_version_and_a_new_event(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)

    first = upload_topology(api, token, SIMPLE_TOPOLOGY)

    changed = copy.deepcopy(SIMPLE_TOPOLOGY)
    changed["nodes"].append(
        {"node_id": "cache", "name": "Cache", "node_type": "cache"}
    )
    changed["edges"].append(
        {"source": "web", "target": "cache", "edge_type": "calls"}
    )
    second = upload_topology(api, token, changed)

    assert second["unchanged"] is False
    assert second["version"] != first["version"]
    assert len(topics(api, token, "topology.updated")) == 2

    listing = api.client.get(
        f"/apps/{APP_ID}/topology", headers=api.auth_header(token)
    ).json()
    assert len(listing["versions"]) == 2
    assert listing["version"] == second["version"]


@pytest.mark.req("REQ-036")
def test_a_declared_version_is_honoured(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)

    document = copy.deepcopy(SIMPLE_TOPOLOGY)
    document["version"] = "2026-09-01.7"
    uploaded = upload_topology(api, token, document)
    assert uploaded["version"] == "2026-09-01.7"


@pytest.mark.req("REQ-036")
def test_a_dangling_edge_refuses_the_whole_document(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)

    good = upload_topology(api, token, SIMPLE_TOPOLOGY)

    broken = copy.deepcopy(SIMPLE_TOPOLOGY)
    broken["edges"].append(
        {"source": "web", "target": "ghost", "edge_type": "calls"}
    )
    refused = upload_topology(api, token, broken, expect=422)
    assert "ghost" in refused["detail"]

    listing = api.client.get(
        f"/apps/{APP_ID}/topology", headers=api.auth_header(token)
    ).json()
    assert listing["version"] == good["version"], "the previous map is untouched"
    assert len(listing["versions"]) == 1


@pytest.mark.req("REQ-036")
def test_upload_counts_toward_readiness(api, observe):
    token, _tenant_id = owner(api)
    register_app(api, token)

    before = api.client.get(
        f"/apps/{APP_ID}/readiness", headers=api.auth_header(token)
    ).json()
    upload_topology(api, token, SIMPLE_TOPOLOGY)
    after = api.client.get(
        f"/apps/{APP_ID}/readiness", headers=api.auth_header(token)
    ).json()

    assert after["score"] == before["score"] + 10
    topology_check = next(
        check for check in after["checks"] if check["key"] == "topology.version"
    )
    assert topology_check["passed"] is True
