"""The reference deployment pipeline posts to the real ingest API (REQ-038, REQ-032).

This is the sprint's acceptance clause about push, exercised end to end without
faking either half. The config repository's `deploy.sh` is run as a subprocess
against a throwaway HTTP server that records exactly what went on the wire; those
bytes and headers - unmodified, not re-serialised - are then replayed at the real
ingest endpoint through the real API.

So what is under test is not "a batch signed the way the tests sign it is
accepted". It is "the bytes a shell script written by an application team,
signing with openssl and posting with curl, are accepted; the same run repeated
is a duplicate; and a signature computed with the wrong secret is refused with
the refusal in the audit chain".
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

import pytest

from signalforge.tests.helpers import bash_executable

from signalforge.contracts.canonical import ChangeStatus, ChangeType
from signalforge.contracts.ingest import (
    HDR_KEY_ID,
    HDR_SIGNATURE,
    HDR_TIMESTAMP,
    INGEST_PATHS,
    IngestChangesRequest,
)
from signalforge.tests.ingest.conftest import events_of, issue_key
from signalforge.tests.reference.conftest import DEPLOY_SCRIPT, ROOT

FORWARDED = ("authorization", "content-type", HDR_TIMESTAMP.lower(),
             HDR_SIGNATURE.lower(), HDR_KEY_ID.lower())


class _Capture(BaseHTTPRequestHandler):
    """Records ingest posts, answers the harness's own deploy call."""

    server_version = "capture/1.0"

    def do_POST(self):  # noqa: N802 (BaseHTTPRequestHandler's name)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        self.server.captured.append(
            {"path": self.path, "headers": dict(self.headers), "body": body}
        )
        if self.path.startswith("/api/deploy/"):
            self._reply(200, {"region": self.path.rsplit("/", 1)[-1], "version": 2, "changed": True})
        else:
            self._reply(202, {"accepted": 1, "duplicates": 0, "rejected": [],
                              "request_id": "captured"})

    def _reply(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        """Silence: the point of this server is what it records, not what it says."""


@pytest.fixture
def capture():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Capture)
    server.captured = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def run_deploy(capture, *, region: str, token: str, secret: str,
               key_id: Optional[str] = None) -> subprocess.CompletedProcess:
    host, port = capture.server_address[0], capture.server_address[1]
    base = f"http://{host}:{port}"
    environment = dict(os.environ)
    environment.update(
        {
            "SIGNALFORGE_INGEST_URL": f"{base}{INGEST_PATHS['changes']}",
            "SIGNALFORGE_INGEST_TOKEN": token,
            "SIGNALFORGE_INGEST_SECRET": secret,
            "PAYSPHERE_URL": base,
            "PAYSPHERE_DEPLOY_KEY": "unused-by-the-capture-server",
        }
    )
    if key_id:
        environment["SIGNALFORGE_INGEST_KEY_ID"] = key_id
    else:
        environment.pop("SIGNALFORGE_INGEST_KEY_ID", None)
    return subprocess.run(
        [bash_executable(), str(DEPLOY_SCRIPT), "--region", region],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=environment,
    )


def ingest_posts(capture) -> list[dict]:
    return [one for one in capture.captured if one["path"] == INGEST_PATHS["changes"]]


def replay(api, captured: dict):
    """Post the recorded bytes at the real endpoint, exactly as they went out."""
    headers = {
        name: value
        for name, value in captured["headers"].items()
        if name.lower() in FORWARDED
    }
    return api.client.post(INGEST_PATHS["changes"], content=captured["body"], headers=headers)


@pytest.fixture
def pipeline_key(api):
    """A real ingest key, and a clock the pipeline's real timestamps fall inside.

    The suite normally runs on a fixed fake clock; a subprocess signing with the
    machine's own `date` would be outside the replay window against it. Moving
    the clock to now is the honest fix - the replay window is being exercised,
    not bypassed.
    """
    api.clock.current = datetime.now(timezone.utc)
    signup = api.signup()
    token = signup["tokens"]["access_token"]
    key = issue_key(api, token, stream="changes", app_id="payments", name="config pipeline")
    return {"owner": token, "tenant": signup["org"]["id"], "key": key}


@pytest.mark.req("REQ-038", "REQ-032")
def test_the_pipeline_announces_a_deployment_the_platform_accepts(api, capture, pipeline_key):
    key = pipeline_key["key"]
    result = run_deploy(
        capture, region="region-b", token=key["token"], secret=key["secret"], key_id=key["id"]
    )
    assert result.returncode == 0, result.stdout + result.stderr

    posts = ingest_posts(capture)
    assert len(posts) == 2, "the pipeline announces a start and an end, and did not"

    for captured in posts:
        assert captured["headers"]["Authorization"] == f"Bearer {key['token']}"
        assert captured["headers"][HDR_TIMESTAMP]
        assert captured["headers"][HDR_SIGNATURE]
        assert captured["headers"][HDR_KEY_ID] == key["id"]
        # It is a canonical batch of canonical ChangeEvents, not a vendor shape
        # the platform would have to normalise.
        batch = IngestChangesRequest.model_validate_json(captured["body"])
        assert len(batch.items) == 1
        change = batch.items[0].payload
        assert change.change_type is ChangeType.DEPLOYMENT
        assert change.version == "v12"
        assert change.service == "payments"

    started = IngestChangesRequest.model_validate_json(posts[0]["body"]).items[0]
    finished = IngestChangesRequest.model_validate_json(posts[1]["body"]).items[0]
    assert started.payload.status is ChangeStatus.STARTED
    assert finished.payload.status is ChangeStatus.COMPLETED
    assert started.payload.change_id == finished.payload.change_id
    assert started.event_id != finished.event_id

    accepted = replay(api, posts[0])
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["accepted"] == 1

    completed = replay(api, posts[1])
    assert completed.status_code == 202, completed.text
    assert completed.json()["accepted"] == 1

    timeline = events_of(api, pipeline_key["owner"], ("deployment.started", "deployment.completed"))
    assert [event["topic"] for event in timeline] == [
        "deployment.started",
        "deployment.completed",
    ]
    assert timeline[0]["payload"]["change_id"] == "v12-region-b"


@pytest.mark.req("REQ-038", "REQ-032")
def test_re_running_the_pipeline_is_a_duplicate_not_a_second_deployment(
    api, capture, pipeline_key
):
    """A pipeline is re-triggered all the time. If each re-trigger became a
    second deployment in the timeline, "what changed just before this incident"
    would be answered with noise."""
    key = pipeline_key["key"]
    assert run_deploy(
        capture, region="region-b", token=key["token"], secret=key["secret"]
    ).returncode == 0
    first = ingest_posts(capture)
    capture.captured.clear()

    assert run_deploy(
        capture, region="region-b", token=key["token"], secret=key["secret"]
    ).returncode == 0
    second = ingest_posts(capture)

    assert (
        IngestChangesRequest.model_validate_json(first[0]["body"]).items[0].event_id
        == IngestChangesRequest.model_validate_json(second[0]["body"]).items[0].event_id
    )

    assert replay(api, first[0]).json()["accepted"] == 1
    repeat = replay(api, second[0])
    assert repeat.status_code == 202, repeat.text
    assert repeat.json() == {**repeat.json(), "accepted": 0, "duplicates": 1}

    timeline = events_of(api, pipeline_key["owner"], ("deployment.started",))
    assert len(timeline) == 1, "the same deployment was recorded twice"
    assert "audit.ingest.duplicate" in api.audit_topics(pipeline_key["tenant"])


@pytest.mark.req("REQ-038", "REQ-032", "REQ-017")
def test_a_forged_signature_from_the_pipeline_is_refused_and_audited(api, capture, pipeline_key):
    """The sprint's acceptance: a forged HMAC is rejected with an audit event.
    Forged here means what it means in the field - a sender holding the right
    token and the wrong secret."""
    key = pipeline_key["key"]
    assert run_deploy(
        capture, region="region-a", token=key["token"], secret="not-the-issued-secret"
    ).returncode == 0

    posts = ingest_posts(capture)
    assert posts, "the pipeline sent nothing to forge"

    refused = replay(api, posts[0])
    assert refused.status_code == 401
    # A bare refusal: a caller that could tell an unknown token from a bad
    # signature from clock skew has an oracle.
    assert refused.json() == {"detail": "authentication failed"}

    timeline = events_of(api, pipeline_key["owner"], ("system.ingest.rejected",))
    assert timeline, "a refusal against a known key left no trace on the timeline"
    assert timeline[-1]["payload"]["code"] == "bad_signature"
    assert timeline[-1]["payload"]["key_id"] == key["id"]
    assert "audit.ingest.rejected" in api.audit_topics(pipeline_key["tenant"])

    assert not events_of(api, pipeline_key["owner"], ("deployment.started",))


@pytest.mark.req("REQ-038")
def test_the_deploy_still_happens_when_the_platform_cannot_be_told(api, capture, pipeline_key):
    """Monitoring does not get to gate shipping. With no ingest credential the
    pipeline applies the change, says loudly that it went unannounced, and exits
    successfully - and the application is still deployed."""
    environment = dict(os.environ)
    host, port = capture.server_address
    environment.update({"PAYSPHERE_URL": f"http://{host}:{port}"})
    environment.pop("SIGNALFORGE_INGEST_TOKEN", None)
    environment.pop("SIGNALFORGE_INGEST_SECRET", None)

    result = subprocess.run(
        [bash_executable(), str(DEPLOY_SCRIPT), "--region", "region-b"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=environment,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "not announced" in result.stderr
    assert "NOT announced to SignalForge" in result.stdout
    assert not ingest_posts(capture), "a pipeline with no credential still posted"
    assert [one["path"] for one in capture.captured] == ["/api/deploy/region-b"]
