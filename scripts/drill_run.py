#!/usr/bin/env python
"""Run a drill against a *live* SignalForge and a live reference harness.

    python scripts/drill_run.py drills/s4-acceptance.yaml \
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

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


def onboard(api: Api, token: str, drill: dict, app_base_url: str) -> None:
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
    for connector in drill["connectors"]:
        api.call(
            "POST",
            "/connectors",
            token=token,
            json={
                "app_id": app_id,
                "adapter_id": connector["adapter_id"],
                "name": f"{app_id} {connector['adapter_id']}",
                "credential_scope": connector["credential_scope"],
                "config": (
                    {"base_url": app_base_url}
                    if connector["targets_the_application"]
                    else {}
                ),
                "credentials": (
                    {"token": connector["credential"]} if connector["credential"] else {}
                ),
            },
        )
    api.call("PUT", f"/apps/{app_id}/mapping", token=token, json=drill["mapping"])
    api.call(
        "PUT", f"/apps/{app_id}/governance", token=token, json={"slos": drill["slos"]}
    )


def place(app_base_url: str, journey: str, region: str, count: int) -> None:
    with httpx.Client(timeout=TIMEOUT_S) as client:
        for _ in range(count):
            try:
                client.post(
                    f"{app_base_url}/api/payments/{journey}",
                    headers={"X-Region": region},
                    json={"amount_minor": 129900},
                )
            except httpx.HTTPError:
                # A payment the application refused is a payment the drill wanted.
                pass


def inject(
    app_base_url: str, drill: dict, credential: str, *, params: Optional[dict] = None
) -> dict:
    chaos = drill["chaos"]
    response = httpx.post(
        f"{app_base_url}/observe/chaos/{chaos['scenario_id']}/inject",
        headers={"Authorization": f"Bearer {credential}"},
        json={
            "params": params if params is not None else chaos["params"],
            "scope": drill["scope"],
            "idempotency_key": f"{chaos['idempotency_key']}-{int(time.time() * 1000)}",
        },
        timeout=TIMEOUT_S,
    )
    response.raise_for_status()
    return response.json()


def heal(app_base_url: str, drill: dict, credential: str) -> None:
    """Put the fault back where it was found, before anything is measured.

    A drill that starts from whatever the last drill left behind measures the
    last drill. The harness expires an injection on its own after the scenario's
    declared maximum duration, which is the safety net; this is the discipline.
    """
    params = drill["chaos"].get("heal_params")
    if not params:
        return
    inject(app_base_url, drill, credential, params=params)
    log("healed whatever a previous run left applied")


def wait_for(api: Api, token: str, app_id: str, *, stance: str, timeout_s: float) -> dict:
    """Poll the detection route until the platform reaches a stance, or give up.

    Reads, never forces: the evaluate route exists but using it here would make
    the walkthrough demonstrate a button rather than the detector's own cadence.
    """
    deadline = time.monotonic() + timeout_s
    last: dict = {}
    while time.monotonic() < deadline:
        last = api.call("GET", f"/apps/{app_id}/detection", token=token)
        if last.get("stance") == stance:
            return last
        time.sleep(2.0)
    return last


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
    body = {
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
    }
    raw = json.dumps(body).encode()
    stamp = str(int(time.time()))
    signature = hmac.new(
        key["secret"].encode(), f"{stamp}.".encode() + raw, hashlib.sha256
    ).hexdigest()
    response = httpx.post(
        f"{api_base.rstrip('/')}/api/v1/ingest/changes",
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


def drill_credential(drill: dict) -> str:
    for connector in drill["connectors"]:
        if connector["adapter_id"].endswith("chaos"):
            return connector["credential"]
    raise SystemExit("this drill binds no chaos connector, so it can inject nothing")


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
    args = parser.parse_args()

    if not args.drill.exists():
        raise SystemExit(f"no such drill: {args.drill}")
    drill = yaml.safe_load(args.drill.read_text(encoding="utf-8"))
    app_id = drill["application"]["app_id"]
    traffic = drill["traffic"]
    regions = list(drill["mapping"]["regions"])

    api = Api(args.api)
    try:
        log(f"signing up {args.email} in {args.org!r}")
        owner = signup(api, args.org, args.email)
        owner_token = owner["tokens"]["access_token"]

        log("onboarding the application through its public contracts")
        onboard(api, owner_token, drill, args.app_base_url)

        log("inviting the people an incident needs")
        ic = invite(
            api,
            owner_token,
            email="ic@drill.test",
            roles=["incident_commander"],
            name="Ida Commander",
        )
        first_sme = invite(
            api, owner_token, email="sme1@drill.test", roles=["sme"], name="Sam SME"
        )
        second_sme = invite(
            api, owner_token, email="sme2@drill.test", roles=["sme"], name="Sasha Second"
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

        heal(args.app_base_url, drill, drill_credential(drill))

        log(f"driving steady traffic for {args.steady_s:.0f}s so a baseline can form")
        deadline = time.monotonic() + args.steady_s
        while time.monotonic() < deadline:
            for region in regions:
                place(
                    args.app_base_url,
                    traffic["journey"],
                    region,
                    traffic["payments_per_region_per_tick"],
                )
            time.sleep(1.0)
        steady = api.call("GET", f"/apps/{app_id}/detection", token=owner_token)
        log(f"stance now {steady.get('stance')!r}, health {steady.get('health', {}).get('band')}")

        if args.no_fault:
            log("--no-fault: stopping before the injection")
        else:
            log(f"injecting {drill['chaos']['scenario_id']} {drill['chaos']['params']}")
            injection = inject(args.app_base_url, drill, drill_credential(drill))
            log(f"injection {injection['injection_id']} is {injection['state']}")

            log("driving customer traffic into the fault")
            for _ in range(int(traffic["fault_ticks"]) * 3):
                for region in regions:
                    place(
                        args.app_base_url,
                        traffic["journey"],
                        region,
                        traffic["fault_payments_per_region_per_tick"],
                    )
                time.sleep(1.0)

            watched = wait_for(
                api, owner_token, app_id, stance="watch", timeout_s=args.watch_timeout_s
            )
            log(f"stance {watched.get('stance')!r}")
            proposed = wait_for(
                api,
                owner_token,
                app_id,
                stance="proposed",
                timeout_s=args.watch_timeout_s,
            )
            severity = proposed.get("severity") or {}
            log(
                f"stance {proposed.get('stance')!r}, proposal "
                f"{severity.get('severity')} corroborated="
                f"{proposed.get('corroboration', {}).get('corroborated')} "
                f"declared={severity.get('declared')}"
            )

        # The change window, from a real deploy-hook call over the frozen ingest
        # path with a real HMAC - the same contract the reference config
        # repository's deploy.sh speaks.
        key = api.call(
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

        print()
        print(json.dumps(
            {
                "org_slug": owner["org"]["slug"],
                "password": PASSWORD,
                "sign_in": {
                    "owner": args.email,
                    "incident_commander": "ic@drill.test",
                    "sme": "sme1@drill.test",
                    "second_sme": "sme2@drill.test",
                    "tech_lead": "lead@drill.test",
                },
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
