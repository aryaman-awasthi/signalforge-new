#!/usr/bin/env python
"""Run a drill against a *live* SignalForge and a live reference harness.

    python scripts/drill_run.py drills/demo-setup.yaml \
        --api http://127.0.0.1:8000 --app-base-url http://127.0.0.1:8080

A drill is data (CLAUDE.md rule 7). This is the runner: it reads one drill file
and performs, over HTTP and against the running product, exactly what the
acceptance test performs in process - sign up, onboard the application through
its public contracts, invite the people an incident needs, drive load, inject
the published fault, and then *stop*, because what happens next is a person
pressing Declare.

It exists so the browser walkthrough is reproducible. Sprint 4's acceptance was
driven by hand over HTTP and nobody clicked; the reason it was never clicked is
that setting a cockpit up to the moment before a declaration takes forty API
calls. This is those calls.

Every key below is optional. A drill that names no people invites nobody; one
that names no topology uploads none. The two drills in this repository are the
two ends of that range: `s4-acceptance.yaml` is the smallest world in which the
corroboration rule can be proved, and `demo-setup.yaml` is the smallest world in
which no panel of the cockpit reads empty.

Nothing here is product code and nothing in the product imports it. It prints
the sign-in details the walkthrough needs and leaves the fault running.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT_S = 30.0

PASSWORD = "drill-strong-password"


def log(message: str) -> None:
    print(f"[drill] {message}", flush=True)


class Api:
    """The platform, over HTTP, as a person's browser would reach it."""

    def __init__(self, base_url: str):
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=TIMEOUT_S)

    def close(self) -> None:
        self._client.close()

    def call(
        self,
        method: str,
        path: str,
        *,
        token: Optional[str] = None,
        expect: tuple[int, ...] = (200, 201),
        **kwargs: Any,
    ) -> dict:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        response = self._client.request(method, path, headers=headers, **kwargs)
        if response.status_code not in expect:
            raise SystemExit(
                f"{method} {path} -> {response.status_code}: {response.text[:400]}"
            )
        if not response.content:
            return {}
        return response.json()


def signup(api: Api, org: str, email: str) -> dict:
    return api.call(
        "POST",
        "/auth/signup",
        json={
            "org_name": org,
            "email": email,
            "password": PASSWORD,
            "display_name": "Owner",
        },
    )


def invite(api: Api, owner_token: str, *, email: str, roles: list[str], name: str) -> dict:
    created = api.call(
        "POST",
        "/invites",
        token=owner_token,
        json={"email": email, "roles": roles, "app_id": None},
    )
    return api.call(
        "POST",
        "/auth/accept-invite",
        json={"token": created["token"], "password": PASSWORD, "display_name": name},
    )


# -- onboarding ---------------------------------------------------------------


def onboard(
    api: Api, token: str, drill: dict, app_base_url: str
) -> tuple[dict[str, str], dict[str, dict]]:
    """Register the application and bind every connector the drill names.

    Returns (adapter_id -> binding_id, stream -> issued ingest key). The bindings
    matter because the later steps - importing actions, normalising a topology
    document - name the binding that should do the work rather than letting the
    platform guess which of several it meant. The keys matter because a push
    connector's HMAC secret is issued by the platform, so the only honest way for
    a drill to bind one is to ask for the key first and hand over what came back.
    """
    app_id = drill["application"]["app_id"]
    api.call(
        "POST",
        "/apps",
        token=token,
        json={
            "app_id": app_id,
            "name": app_id.title(),
            "tier": drill["application"]["tier"],
            "base_url": app_base_url,
        },
    )
    # The mapping goes in before the first connector is bound, and the order is
    # load-bearing. A metrics connector starts being scraped by the health loop
    # the moment it exists; if the region label is not yet configured when that
    # first scrape lands, its samples are stored under no region at all. The
    # detector then honestly reports a third region called "" that has one family
    # in it and no data, and that region - being the worst - is what the golden
    # tiles and the health score show. Mapping first costs nothing and the
    # phantom region never exists.
    api.call("PUT", f"/apps/{app_id}/mapping", token=token, json=drill["mapping"])
    signals = drill["mapping"].get("signals") or []
    log(
        f"mapped {len(signals)} golden signals, "
        f"{len(drill['mapping'].get('journeys') or [])} journeys, "
        f"{len(drill['mapping'].get('probes') or [])} probes"
    )

    bindings: dict[str, str] = {}
    keys: dict[str, dict] = {}
    for connector in drill["connectors"]:
        config: dict[str, Any] = {}
        if connector.get("targets_the_application"):
            config["base_url"] = app_base_url
        config.update(connector.get("config") or {})

        credentials: dict[str, str] = {}
        if connector.get("credential"):
            credentials["token"] = connector["credential"]
        stream = connector.get("ingest_stream")
        if stream:
            keys[stream] = api.call(
                "POST",
                "/ingest-keys",
                token=token,
                json={
                    "app_id": app_id,
                    "stream": stream,
                    "name": f"{app_id} {stream} pipeline",
                },
            )
            credentials["ingest_secret"] = keys[stream]["secret"]

        created = api.call(
            "POST",
            "/connectors",
            token=token,
            json={
                "app_id": app_id,
                "adapter_id": connector["adapter_id"],
                "name": f"{app_id} {connector['adapter_id']}",
                "credential_scope": connector["credential_scope"],
                "config": config,
                "credentials": credentials,
            },
        )
        bindings[connector["adapter_id"]] = created.get("id", "")
    log(f"bound {len(bindings)} connectors")

    governance: dict[str, Any] = {}
    if drill.get("slos"):
        governance["slos"] = drill["slos"]
    if governance:
        api.call("PUT", f"/apps/{app_id}/governance", token=token, json=governance)
    return bindings, keys


def import_actions(
    api: Api, token: str, drill: dict, bindings: dict[str, str]
) -> list[dict]:
    """Import what the application says it can do, then re-assert the floor.

    The import never makes anything executable and never lowers a risk level.
    The overrides below exist to be checked: a demo whose safety story depends on
    an operator having remembered to raise a risk level is not a safety story.
    """
    spec = drill.get("actions")
    if not spec or not spec.get("import"):
        return []
    app_id = drill["application"]["app_id"]
    binding_id = bindings.get("http-observe-actions")
    result = api.call(
        "POST",
        f"/apps/{app_id}/actions/import",
        token=token,
        json={"binding_id": binding_id} if binding_id else {},
    )
    actions = result.get("actions") or []
    for override in spec.get("overrides") or []:
        api.call(
            "PATCH",
            f"/apps/{app_id}/actions/{override['action_id']}",
            token=token,
            json={"risk_override": override["risk_override"]},
        )
    expected_high = set(spec.get("expect_high_risk") or [])
    if expected_high:
        profile = api.call("GET", f"/apps/{app_id}", token=token)
        by_id = {
            action["action_id"]: action
            for action in (profile.get("profile", {}).get("actions") or actions)
        }
        for action_id in sorted(expected_high):
            action = by_id.get(action_id)
            effective = (action or {}).get("effective_risk") or (action or {}).get(
                "manifest_risk"
            )
            if effective != "high":
                raise SystemExit(
                    f"the floor did not hold: {action_id} is {effective!r}, not high risk"
                )
    log(f"imported {len(actions)} actions; {len(expected_high)} confirmed high risk")
    return actions


def capture_topology(app_base_url: str, spec: dict) -> dict:
    """Read the application's own map over its own public contract."""
    response = httpx.get(
        f"{app_base_url.rstrip('/')}{spec['path']}",
        headers=(
            {"Authorization": f"Bearer {spec['credential']}"}
            if spec.get("credential")
            else {}
        ),
        timeout=TIMEOUT_S,
    )
    response.raise_for_status()
    return response.json()


def upload_topology(
    api: Api, token: str, app_id: str, document: dict, binding_id: Optional[str]
) -> dict:
    return api.call(
        "PUT",
        f"/apps/{app_id}/topology",
        token=token,
        json={
            "document": document,
            **({"binding_id": binding_id} if binding_id else {}),
        },
    )


# -- people -------------------------------------------------------------------


def invite_people(api: Api, owner_token: str, drill: dict) -> dict[str, dict]:
    """One person per role the incident will name. Returns email -> user."""
    people: dict[str, dict] = {}
    for person in drill.get("people") or []:
        accepted = invite(
            api,
            owner_token,
            email=person["email"],
            roles=person["roles"],
            name=person["name"],
        )
        people[person["email"]] = accepted["user"]
    if people:
        log(f"invited {len(people)} people, one per role an incident needs")
    return people


def set_oncall(api: Api, owner_token: str, drill: dict, people: dict[str, dict]) -> None:
    """The rotation is who is on call; the policy is what happens when nobody answers."""
    spec = drill.get("oncall")
    if not spec:
        return
    app_id = drill["application"]["app_id"]
    rotation_id: Optional[str] = None
    rotation = spec.get("rotation")
    if rotation:
        members = [
            people[email]["id"] for email in rotation["members"] if email in people
        ]
        if not members:
            return
        created = api.call(
            "POST",
            "/oncall/rotations",
            token=owner_token,
            json={
                "name": rotation["name"],
                "app_id": app_id,
                "user_ids": members,
                "handoff_interval_hours": rotation.get("handoff_interval_hours", 168),
            },
        )
        rotation_id = created["id"]

    escalation = spec.get("escalation")
    if not escalation:
        return
    steps = []
    for step in escalation["steps"]:
        target = step["target"]
        if target == "rotation":
            if not rotation_id:
                continue
            steps.append(
                {
                    "target_type": "rotation",
                    "target_id": rotation_id,
                    "timeout_s": step["timeout_s"],
                }
            )
        elif target in people:
            steps.append(
                {
                    "target_type": "user",
                    "target_id": people[target]["id"],
                    "timeout_s": step["timeout_s"],
                }
            )
    if not steps:
        return
    api.call(
        "POST",
        "/escalation/policies",
        token=owner_token,
        json={"name": escalation["name"], "app_id": app_id, "steps": steps},
    )
    log(f"on-call rotation and a {len(steps)}-step escalation policy set")


# -- the world ----------------------------------------------------------------


def place(app_base_url: str, journey: str, region: str, count: int) -> None:
    """`count` payments, placed at the same time rather than one after another.

    Concurrency is the point, not a speed-up. A connection-pool fault is a
    *queueing* fault: shrink the pool to two and a caller who waits for each
    reply before sending the next never queues for anything, so the saturation
    gauge stays flat and the p95 never moves, and the scenario the application
    publishes as "payments queue for a connection" demonstrates nothing.
    Sequential load measures an application no real customer base resembles.
    """
    def one(client: httpx.Client) -> None:
        try:
            client.post(
                f"{app_base_url}/api/payments/{journey}",
                headers={"X-Region": region},
                json={"amount_minor": 129900},
            )
        except httpx.HTTPError:
            # A payment the application refused is a payment the drill wanted.
            pass

    with httpx.Client(timeout=TIMEOUT_S) as client:
        with ThreadPoolExecutor(max_workers=min(count, 16)) as pool:
            list(pool.map(lambda _: one(client), range(count)))


def journeys_of(drill: dict) -> list[str]:
    traffic = drill["traffic"]
    if traffic.get("journeys"):
        return list(traffic["journeys"])
    return [traffic["journey"]]


def scenarios_of(drill: dict) -> list[dict]:
    """`chaos` is one scenario or several. Both shapes read the same here."""
    chaos = drill.get("chaos")
    if not chaos:
        return []
    return list(chaos) if isinstance(chaos, list) else [chaos]


def inject(
    app_base_url: str,
    scenario: dict,
    scope: str,
    credential: str,
    *,
    params: Optional[dict] = None,
) -> dict:
    response = httpx.post(
        f"{app_base_url}/observe/chaos/{scenario['scenario_id']}/inject",
        headers={"Authorization": f"Bearer {credential}"},
        json={
            "params": params if params is not None else scenario["params"],
            "scope": scope,
            "idempotency_key": (
                f"{scenario['idempotency_key']}-{int(time.time() * 1000)}"
            ),
        },
        timeout=TIMEOUT_S,
    )
    response.raise_for_status()
    return response.json()


def heal(app_base_url: str, drill: dict, credential: str) -> None:
    """Put every fault back where it was found, before anything is measured.

    A drill that starts from whatever the last drill left behind measures the
    last drill. The harness expires an injection on its own after the scenario's
    declared maximum duration, which is the safety net; this is the discipline.
    """
    healed = 0
    for scenario in scenarios_of(drill):
        params = scenario.get("heal_params")
        if not params:
            continue
        inject(app_base_url, scenario, drill["scope"], credential, params=params)
        healed += 1
    if healed:
        log(f"healed {healed} fault(s) a previous run may have left applied")


def wait_for(
    api: Api,
    token: str,
    app_id: str,
    *,
    stance: str,
    timeout_s: float,
    meanwhile=None,
) -> dict:
    """Poll the detection route until the platform reaches a stance, or give up.

    Reads, never forces: the evaluate route exists but using it here would make
    the walkthrough demonstrate a button rather than the detector's own cadence.

    `meanwhile` keeps customers arriving while the platform makes up its mind,
    and it is not decoration. A rate signal is judged against a rolling baseline,
    and a rolling baseline eventually absorbs a fault that never stops - which is
    correct, and is why the detector reports "breaching" separately from
    "anomalous". Stopping the load and then waiting three minutes for a verdict
    measures the baseline catching up rather than the fault. Real customers do
    not stop arriving because something broke.
    """
    deadline = time.monotonic() + timeout_s
    last: dict = {}
    while time.monotonic() < deadline:
        last = api.call("GET", f"/apps/{app_id}/detection", token=token)
        if last.get("stance") == stance:
            return last
        if meanwhile is not None:
            meanwhile()
        time.sleep(2.0)
    return last


def post_signed(api_base: str, path: str, key: dict, body: dict) -> dict:
    """One signed batch at a frozen ingest path.

    The signature is computed over the exact bytes on the wire, because that is
    what the platform verifies - serialising twice would produce a valid-looking
    request the receiver refuses, and it would refuse it correctly.
    """
    raw = json.dumps(body).encode()
    stamp = str(int(time.time()))
    signature = hmac.new(
        key["secret"].encode(), f"{stamp}.".encode() + raw, hashlib.sha256
    ).hexdigest()
    response = httpx.post(
        f"{api_base.rstrip('/')}{path}",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key['token']}",
            "X-SignalForge-Signature": signature,
            "X-SignalForge-Timestamp": stamp,
            "X-SignalForge-Key-Id": key["id"],
        },
        timeout=TIMEOUT_S,
    )
    response.raise_for_status()
    return response.json()


def announce_change(api_base: str, drill: dict, key: dict) -> dict:
    """A deploy hook, signed the way the frozen ingest contract says.

    Started and never completed, on purpose: an unfinished change is what the
    chip beside the health score is for, and "what changed just before this
    broke" is the first question anybody asks in a war room.
    """
    change = drill.get("change_window")
    if not change:
        return {}
    now = datetime.now(timezone.utc)
    return post_signed(
        api_base,
        "/api/v1/ingest/changes",
        key,
        {
            "source_system": change.get("actor", "pipeline"),
            "simulated": False,
            "items": [
                {
                    "event_id": str(uuid.uuid4()),
                    "occurred_at": now.isoformat(),
                    "payload": {
                        "change_id": change["change_id"],
                        "change_type": change["change_type"],
                        "service": change["service"],
                        "status": "started",
                        "started_at": now.isoformat(),
                        "version": change.get("version"),
                        "actor": change.get("actor"),
                    },
                }
            ],
        },
    )


def fire_alerts(api_base: str, drill: dict, key: dict) -> dict:
    """The alerts a monitoring system would be firing about this fault.

    They matter for one specific reason: the golden signals are measured on the
    application as a whole and so name no service, while an alert carries a node
    label. That label is what turns a measured fault into a confirmed point on
    the topology, and everything downstream of it into a predicted one.
    """
    spec = drill.get("alerts")
    if not spec or not spec.get("items"):
        return {}
    now = datetime.now(timezone.utc)
    items = []
    for alert in spec["items"]:
        starts = now - timedelta(seconds=float(alert.get("after_s", 0)))
        items.append(
            {
                "event_id": str(uuid.uuid4()),
                "occurred_at": starts.isoformat(),
                "payload": {
                    "alert_id": alert["alert_id"],
                    "name": alert["name"],
                    "severity": alert.get("severity", "warning"),
                    "status": alert.get("status", "firing"),
                    "starts_at": starts.isoformat(),
                    "labels": alert.get("labels") or {},
                    "annotations": alert.get("annotations") or {},
                },
            }
        )
    return post_signed(
        api_base,
        "/api/v1/ingest/alerts",
        key,
        {
            "source_system": spec.get("source_system", "monitoring"),
            "simulated": bool(spec.get("simulated", True)),
            "items": items,
        },
    )


def drill_credential(drill: dict) -> str:
    for connector in drill["connectors"]:
        if connector["adapter_id"].endswith("chaos"):
            return connector["credential"]
    raise SystemExit("this drill binds no chaos connector, so it can inject nothing")


def drive(app_base_url: str, drill: dict, regions: Iterable[str], per_tick: int) -> None:
    for journey in journeys_of(drill):
        for region in regions:
            place(app_base_url, journey, region, per_tick)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("drill", type=Path)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--app-base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--org", default="Drill Co")
    parser.add_argument("--email", default="owner@drill.test")
    parser.add_argument(
        "--steady-s",
        type=float,
        default=75.0,
        help="How long to let the detector watch a healthy application before the "
        "fault. A rolling baseline needs about a minute of buckets.",
    )
    parser.add_argument("--watch-timeout-s", type=float, default=180.0)
    parser.add_argument(
        "--no-fault",
        action="store_true",
        help="Set the cockpit up and stop before injecting anything.",
    )
    parser.add_argument(
        "--refault",
        action="store_true",
        help="Re-inject this drill's faults into a world that is already set up, "
        "and do nothing else. Every scenario the application publishes carries a "
        "max_duration_s that the application enforces itself, so a fault reverts "
        "on its own after ten or fifteen minutes - which is the harness bounding "
        "its own blast radius and is correct. A walkthrough that runs longer than "
        "that re-arms with this rather than by onboarding a second organisation.",
    )
    args = parser.parse_args()

    if not args.drill.exists():
        raise SystemExit(f"no such drill: {args.drill}")
    drill = yaml.safe_load(args.drill.read_text(encoding="utf-8"))
    app_id = drill["application"]["app_id"]
    traffic = drill["traffic"]
    regions = list(drill["mapping"]["regions"])

    if args.refault:
        for scenario in scenarios_of(drill):
            injection = inject(
                args.app_base_url, scenario, drill["scope"], drill_credential(drill)
            )
            log(
                f"re-injected {scenario['scenario_id']} {scenario['params']} "
                f"-> {injection['injection_id']} {injection['state']}"
            )
        for _ in range(int(traffic["fault_ticks"]) * 3):
            drive(
                args.app_base_url,
                drill,
                regions,
                int(traffic["fault_payments_per_region_per_tick"]),
            )
            time.sleep(1.0)
        log("the world is broken again; the incident record was never touched")
        return 0

    # The world is put back before the platform is allowed to look at it, and
    # before rather than after onboarding. The application's own success ratio
    # and p95 are computed over its last N payments, so a previous run's failures
    # stay in that window until enough good payments push them out. Healing and
    # then onboarding into a still-recovering application teaches the detector a
    # baseline of "broken is normal", and every later breach then reads healthy.
    # On a machine that ran reset.sh this costs a few seconds and changes
    # nothing; on one that did not, it is the difference between a demo and a
    # demo of the last demo.
    heal(args.app_base_url, drill, drill_credential(drill))
    flush_ticks = int(traffic.get("flush_ticks", 20))
    if flush_ticks:
        log(f"flushing the application's own rolling windows ({flush_ticks} ticks)")
        for _ in range(flush_ticks):
            drive(
                args.app_base_url,
                drill,
                regions,
                int(traffic["payments_per_region_per_tick"]),
            )

    api = Api(args.api)
    try:
        log(f"signing up {args.email} in {args.org!r}")
        owner = signup(api, args.org, args.email)
        owner_token = owner["tokens"]["access_token"]

        log("onboarding the application through its public contracts")
        bindings, keys = onboard(api, owner_token, drill, args.app_base_url)
        import_actions(api, owner_token, drill, bindings)

        topology_spec = drill.get("topology")
        if topology_spec:
            document = capture_topology(args.app_base_url, topology_spec)
            uploaded = upload_topology(
                api,
                owner_token,
                app_id,
                document,
                bindings.get("topo-import"),
            )
            log(
                f"topology v{uploaded['version']} imported: "
                f"{uploaded['node_count']} nodes, {uploaded['edge_count']} edges, "
                f"normalised by {uploaded['normalized_by']}"
            )

        log("inviting the people an incident needs")
        people = invite_people(api, owner_token, drill)
        if not people:
            # The older drills name no people; they still need somebody to page.
            first_sme = invite(
                api, owner_token, email="sme1@drill.test", roles=["sme"], name="Sam SME"
            )
            second_sme = invite(
                api,
                owner_token,
                email="sme2@drill.test",
                roles=["sme"],
                name="Sasha Second",
            )
            invite(
                api,
                owner_token,
                email="ic@drill.test",
                roles=["incident_commander"],
                name="Ida Commander",
            )
            invite(
                api,
                owner_token,
                email="lead@drill.test",
                roles=["tech_lead"],
                name="Lee Lead",
            )
            api.call(
                "POST",
                "/oncall/rotations",
                token=owner_token,
                json={
                    "name": f"{app_id} primary",
                    "app_id": app_id,
                    "user_ids": [first_sme["user"]["id"], second_sme["user"]["id"]],
                },
            )
        else:
            set_oncall(api, owner_token, drill, people)

        log(f"driving steady traffic for {args.steady_s:.0f}s so a baseline can form")
        deadline = time.monotonic() + args.steady_s
        while time.monotonic() < deadline:
            drive(
                args.app_base_url,
                drill,
                regions,
                int(traffic["payments_per_region_per_tick"]),
            )
            time.sleep(1.0)
        steady = api.call("GET", f"/apps/{app_id}/detection", token=owner_token)
        log(
            f"stance now {steady.get('stance')!r}, "
            f"health {steady.get('health', {}).get('band')}"
        )

        # The baseline the wizard's step 6 learns, so the golden tiles have
        # something to compare against rather than only a current value.
        api.call("POST", f"/apps/{app_id}/baseline", token=owner_token, json={"samples": 5})

        # The change window first, so "what changed just before this broke" is
        # already on the screen when the fault lands rather than after it.
        key = keys.get("changes") or api.call(
            "POST",
            "/ingest-keys",
            token=owner_token,
            json={"app_id": app_id, "stream": "changes", "name": "drill pipeline"},
        )
        announced = announce_change(args.api, drill, key)
        if announced:
            log(f"deployment announced: {announced}")
            # The chip is a projection off the bus, so it lights when the
            # consumer group reaches the event rather than when the POST
            # returns. Waiting for it here is the difference between a demo that
            # shows the chip and one that shows an empty panel.
            window: dict = {}
            deadline = time.monotonic() + 30.0
            while time.monotonic() < deadline:
                window = api.call(
                    "GET", f"/apps/{app_id}/change-window", token=owner_token
                )
                if window.get("open"):
                    break
                time.sleep(1.0)
            log(f"change window open: {window.get('open')}")

        if args.no_fault:
            log("--no-fault: stopping before the injection")
        else:
            for scenario in scenarios_of(drill):
                log(f"injecting {scenario['scenario_id']} {scenario['params']}")
                injection = inject(
                    args.app_base_url,
                    scenario,
                    drill["scope"],
                    drill_credential(drill),
                )
                log(f"injection {injection['injection_id']} is {injection['state']}")

            alert_key = keys.get("alerts")
            if alert_key:
                fired = fire_alerts(args.api, drill, alert_key)
                log(f"alerts fired: {fired}")

            log("driving customer traffic into the fault")
            for _ in range(int(traffic["fault_ticks"]) * 3):
                drive(
                    args.app_base_url,
                    drill,
                    regions,
                    int(traffic["fault_payments_per_region_per_tick"]),
                )
                time.sleep(1.0)

            def keep_customers_arriving() -> None:
                drive(
                    args.app_base_url,
                    drill,
                    regions,
                    int(traffic["fault_payments_per_region_per_tick"]),
                )

            watched = wait_for(
                api,
                owner_token,
                app_id,
                stance="watch",
                timeout_s=args.watch_timeout_s,
                meanwhile=keep_customers_arriving,
            )
            log(f"stance {watched.get('stance')!r}")
            proposed = wait_for(
                api,
                owner_token,
                app_id,
                stance="proposed",
                timeout_s=args.watch_timeout_s,
                meanwhile=keep_customers_arriving,
            )
            severity = proposed.get("severity") or {}
            log(
                f"stance {proposed.get('stance')!r}, proposal "
                f"{severity.get('severity')} corroborated="
                f"{proposed.get('corroboration', {}).get('corroborated')} "
                f"declared={severity.get('declared')}"
            )

            # The application's own runtime configuration is part of its own map,
            # and the injection really did change it. Re-reading now is what
            # gives Affected Services two versions to show a delta between.
            if topology_spec and topology_spec.get("recapture_after_fault"):
                document = capture_topology(args.app_base_url, topology_spec)
                uploaded = upload_topology(
                    api, owner_token, app_id, document, bindings.get("topo-import")
                )
                log(
                    f"topology re-read after the fault: v{uploaded['version']}"
                    + (" (unchanged)" if uploaded.get("unchanged") else " (new version)")
                )

        sign_in = {"owner": args.email}
        for person in drill.get("people") or []:
            sign_in[person["roles"][0] + " · " + person["name"]] = person["email"]
        if not drill.get("people"):
            sign_in.update(
                {
                    "incident_commander": "ic@drill.test",
                    "sme": "sme1@drill.test",
                    "second_sme": "sme2@drill.test",
                    "tech_lead": "lead@drill.test",
                }
            )

        print()
        print(json.dumps(
            {
                "org_slug": owner["org"]["slug"],
                "password": PASSWORD,
                "sign_in": sign_in,
                "app_id": app_id,
                "ingest_key_id": key.get("id"),
                "ingest_key": key.get("token"),
                "ingest_secret": key.get("secret"),
                "next": [
                    f"open /apps/{app_id} and read the stance",
                    "declare from the proposal sheet as the incident commander",
                    "acknowledge on /my/pages as the SME",
                ],
            },
            indent=2,
        ))
        return 0
    finally:
        api.close()


if __name__ == "__main__":
    sys.exit(main())
