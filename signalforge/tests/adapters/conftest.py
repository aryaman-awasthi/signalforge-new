"""Fixtures for the adapter registry suite (S3-T01).

The registry reads a directory, so these tests write real manifest.yaml files
into a temp tree and point a real FileAdapterRegistry at it. Nothing is mocked
away: a test that "installs an adapter" copies a folder, exactly like a
deployment does. Only the adapter runtimes are stubs, because a test cannot
stand up a customer's monitoring stack.

The adapters written here are fixtures, not product code - they exist to
exercise the registry, and the generic adapters this platform ships arrive with
S3-T02.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import httpx
import pytest
import yaml

from signalforge.contracts.canonical import HealthReport, MetricSeries
from signalforge.contracts.spi import ActionsAdapter, HealthAdapter, MetricsAdapter

# --- manifest documents ------------------------------------------------------

PULL_METRICS = {
    "id": "probe-metrics",
    "name": "Probe metrics",
    "version": "1.0.0",
    "kind": "metrics",
    "mode": "pull",
    "vendor": "generic",
    "description": "Reads metrics over the observe contract.",
    "capabilities": ["query_instant", "query_range"],
    "credential_scopes": ["read_only"],
    "auth": {"method": "bearer", "credential_fields": ["token"], "header": "Authorization"},
    "config_schema": {
        "type": "object",
        "required": ["base_url"],
        "additionalProperties": False,
        "properties": {
            "base_url": {"type": "string", "minLength": 1},
            "region_label": {"type": "string"},
        },
    },
    "limits": {"timeout_s": 5, "max_calls_per_minute": 60, "max_result_bytes": 65536,
               "max_rows": 500},
}

PULL_HEALTH = {
    "id": "probe-health",
    "name": "Probe health",
    "version": "2.1.0",
    "kind": "health",
    "mode": "pull",
    "capabilities": ["get_health"],
    "credential_scopes": ["read_only"],
    "config_schema": {
        "type": "object",
        "required": ["base_url"],
        "properties": {"base_url": {"type": "string"}},
    },
}

INVOKE_ACTIONS = {
    "id": "probe-actions",
    "name": "Probe actions",
    "version": "1.0.0",
    "kind": "actions",
    "mode": "invoke",
    "capabilities": ["manifest", "execute", "get_execution"],
    "credential_scopes": ["remediation"],
    "auth": {"method": "api_key", "credential_fields": ["api_key"]},
    "config_schema": {"type": "object", "properties": {"base_url": {"type": "string"}}},
}

PUSH_ALERTS = {
    "id": "probe-alert-hook",
    "name": "Probe alert webhook",
    "version": "1.0.0",
    "kind": "alerts",
    "mode": "push",
    "capabilities": ["normalize"],
    "credential_scopes": ["read_only"],
}

INTERFACE_ONLY = {
    "id": "probe-enterprise-logs",
    "name": "Probe enterprise logs",
    "version": "0.1.0",
    "kind": "logs",
    "mode": "pull",
    "availability": "available",
    "capabilities": ["search"],
    "credential_scopes": ["read_only"],
    "redaction": {"policy_id": "logs-default", "fields": ["message"]},
}

SIMULATED_CHAOS = {
    "id": "probe-chaos",
    "name": "Probe chaos",
    "version": "1.0.0",
    "kind": "chaos",
    "mode": "invoke",
    "simulated": True,
    "capabilities": ["manifest", "inject", "status", "abort"],
    "credential_scopes": ["drill"],
}

ALL_FIXTURE_MANIFESTS = [
    PULL_METRICS,
    PULL_HEALTH,
    INVOKE_ACTIONS,
    PUSH_ALERTS,
    INTERFACE_ONLY,
    SIMULATED_CHAOS,
]


def write_manifest(root: Path, document: dict[str, Any], folder: Optional[str] = None) -> Path:
    directory = root / (folder or str(document.get("id", "broken")))
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "manifest.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def write_adapters_tree(root: Path, documents=ALL_FIXTURE_MANIFESTS) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for document in documents:
        write_manifest(root, document)
    return root


# --- adapter runtimes used by the health tests -------------------------------


class StubMetricsAdapter(MetricsAdapter):
    """A runtime whose ping the test drives. `context` is kept so a test can
    assert exactly what the platform handed the adapter - which is how the
    credential-plumbing test proves the decrypted value goes there and nowhere
    else."""

    def __init__(self, context, behaviour="ok"):
        self.context = context
        self.behaviour = behaviour
        self.pings = 0

    async def ping(self) -> bool:
        self.pings += 1
        if callable(self.behaviour):
            return await self.behaviour()
        if self.behaviour == "ok":
            return True
        if self.behaviour == "false":
            return False
        raise self.behaviour

    async def query_instant(self, query: str, at) -> list[MetricSeries]:
        return []

    async def query_range(self, query, start, end, step_s) -> list[MetricSeries]:
        return []


class StubHealthAdapter(HealthAdapter):
    """Used where a test needs a runtime whose SPI kind disagrees with the
    manifest it is registered against."""

    def __init__(self, context):
        self.context = context

    async def ping(self) -> bool:
        return True

    async def get_health(self) -> HealthReport:
        raise NotImplementedError

    async def run_probe(self, probe):
        raise NotImplementedError


class StubActionsAdapter(ActionsAdapter):
    """A remediation-scoped runtime, used by the credential-scope security tests."""

    def __init__(self, context):
        self.context = context

    async def ping(self) -> bool:
        return True

    async def manifest(self):
        return []

    async def execute(self, action_id, params, idempotency_key):
        raise NotImplementedError

    async def get_execution(self, idempotency_key):
        return None


class ObserveHarness:
    """The integrated systems an adapter suite talks to, and the wiring that
    points a container's adapters at them.

    The adapters reach them over an ASGI transport rather than a mock, so the
    request line, the auth header, the status handling and the size limit are
    all exercised for real; only the socket is elided. Hosts that were never
    mounted refuse the connection, which is how "the target is unreachable" is
    exercised without a firewall.
    """

    BASE_URL = "http://app.test"
    PROMETHEUS_URL = "http://prom.test"

    def __init__(self):
        self.apps: dict[str, object] = {}
        self.timing_out: set[str] = set()
        self.state = None
        self.prometheus = None

    def mount(self, url: str, app) -> None:
        self.apps[httpx.URL(url).host] = httpx.ASGITransport(app=app)

    def attach(self, container) -> None:
        container.adapter_services.client_factory = self.client_factory

    def times_out(self, path: str) -> None:
        """Stage a read timeout on one path.

        Staged at the transport rather than by making the application sleep,
        because that is where a timeout actually comes from - and because an
        in-process ASGI transport has no socket to time out on, so a sleeping
        handler would test the test rather than the adapter.
        """
        self.timing_out.add(path)

    def client_factory(self, timeout_s: float):
        return httpx.AsyncClient(
            transport=_StagedTransport(self.apps, self.timing_out),
            timeout=httpx.Timeout(timeout_s),
        )


class _StagedTransport(httpx.AsyncBaseTransport):
    """Routes to the mounted application for the request's host."""

    def __init__(self, apps: dict[str, object], timing_out: set[str]):
        self._apps = apps
        self._timing_out = timing_out

    async def handle_async_request(self, request):
        if request.url.path in self._timing_out:
            raise httpx.ReadTimeout("staged read timeout", request=request)
        inner = self._apps.get(request.url.host)
        if inner is None:
            raise httpx.ConnectError(
                f"nothing is listening on {request.url.host}", request=request
            )
        return await inner.handle_async_request(request)


@pytest.fixture
def observe(api):
    """The integrated-system test doubles, attached to the API's container."""
    from signalforge.tests.adapters.observe_app import (
        build_observe_app,
        build_prometheus_app,
    )

    harness = ObserveHarness()
    app, state = build_observe_app()
    harness.mount(ObserveHarness.BASE_URL, app)
    harness.state = state
    prom_app, prom_state = build_prometheus_app()
    harness.mount(ObserveHarness.PROMETHEUS_URL, prom_app)
    harness.prometheus = prom_state
    harness.attach(api.container)
    return harness


def connect(
    api,
    token: str,
    adapter_id: str,
    *,
    app_id: str = "checkout",
    scope: Optional[str] = None,
    config: Optional[dict] = None,
    credentials: Optional[dict] = None,
    name: Optional[str] = None,
    expect: int = 201,
) -> dict:
    """Bind an application to a shipped adapter through the real API.

    Everything downstream - the encrypted credential, the validated config, the
    runtime lookup - then happens exactly as it does in production, which is the
    point: these tests never construct an adapter by hand.
    """
    manifest = api.container.adapter_registry.get(adapter_id)
    body = {
        "app_id": app_id,
        "adapter_id": adapter_id,
        "name": name or f"{app_id} {adapter_id}",
        "credential_scope": scope or manifest.credential_scopes[0].value,
        "config": config if config is not None else default_config(manifest),
        "credentials": (
            credentials
            if credentials is not None
            else {field: "app-token" for field in manifest.auth.credential_fields}
        ),
    }
    response = api.client.post("/connectors", json=body, headers=api.auth_header(token))
    assert response.status_code == expect, response.text
    return response.json()


def default_config(manifest) -> dict:
    schema = dict(manifest.config_schema or {})
    required = list(schema.get("required") or [])
    config: dict[str, Any] = {}
    if "base_url" in required:
        config["base_url"] = ObserveHarness.BASE_URL
    return config


@pytest.fixture
def adapters_root(tmp_path) -> Path:
    return write_adapters_tree(tmp_path / "adapters")


@pytest.fixture
def registry(adapters_root):
    from signalforge.infrastructure.adapters.registry import FileAdapterRegistry

    return FileAdapterRegistry(adapters_root)


@pytest.fixture
def adapter_api(tmp_path, adapters_root):
    """The API with a registry pointed at the fixture adapters tree.

    Mirrors the shared `api` fixture, but the container is built with a
    Settings whose adapters_dir is the temp tree - which is the same knob a
    deployment turns to mount an adapter bundle.
    """
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from signalforge.api import deps
    from signalforge.api.main import app
    from signalforge.infrastructure.persistence.models import Base
    from signalforge.tests.helpers import (
        ApiCtx,
        FakeClock,
        make_test_llm_settings,
        make_test_settings,
    )

    db_path = tmp_path / "adapters.db"
    sync_engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()

    settings = make_test_settings(
        f"sqlite+aiosqlite:///{db_path}", adapters_dir=str(adapters_root)
    )
    clock = FakeClock()
    engine = create_async_engine(settings.db_url, poolclass=NullPool)
    container = deps.Container(
        settings=settings,
        engine=engine,
        clock=clock,
        llm_settings=make_test_llm_settings(),
    )
    app.dependency_overrides[deps.get_container] = lambda: container
    try:
        with TestClient(app) as client:
            yield ApiCtx(client=client, container=container, clock=clock, db_path=db_path)
    finally:
        app.dependency_overrides.clear()
