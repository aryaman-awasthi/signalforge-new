# Sprint 9 log (2026-09-02, one session)
Goal: a Spring Boot application can be onboarded without being changed.

Every onboarding path this product had until now asked something of the
application: serve twelve `/observe/*` paths, or post to the ingest API with an
HMAC. That is a small ask for a team that owns its service and an impossible one
for a team that does not - the service whose owner left, the service in a change
freeze, the service whose next release is in March. Both are exactly the services
an incident command centre most needs to see.

A Spring Boot application with Micrometer on its classpath is already serving
`/actuator/prometheus` and `/actuator/health`. This sprint reads them.

## Built

- **[S9-T01][REQ-031, REQ-030] The Spring Actuator adapter family.**
  `infrastructure/adapters/actuator/{__init__.py,adapters.py}` with
  `metrics/manifest.yaml` and `health/manifest.yaml`, two entries in
  `builtin.py`, and the new shared `infrastructure/adapters/probes.py`.

  Two adapters, not one, for the reason the observe set is six: a binding is per
  capability set and per credential scope, and the two endpoints fail
  independently. One "actuator" binding would report a metrics scrape healthy
  because `/actuator/health` answered, which is the specific lie the whole
  ping-the-endpoint-you-read rule exists to prevent.

  | Adapter | Kind | Reads | Capabilities |
  |---|---|---|---|
  | `spring-actuator-metrics` | metrics | `GET /actuator/prometheus` | `query_instant` |
  | `spring-actuator-health` | health | `GET /actuator/health` | `get_health`, `run_probe` |

  This is a *generic vendor* adapter family in the sense `prometheus` is: it
  names a framework, never an application, and it is configured entirely by a
  binding's `base_url`. Both declare `auth.method: none`, because that is how
  Actuator ships; a deployment that has put a gateway in front of it binds the
  observe adapters instead.

  The metrics runtime reuses `domain/integrations/promtext.py` unchanged -
  Micrometer writes the same text exposition format the observe contract freezes,
  so exactly one piece of code in this platform parses it, and the selector
  subset (`=`, `!=`, `=~`, `!~`) works identically. Its test data is shaped the
  way Micrometer really writes: the `_total` suffix, a timer's
  `_count`/`_sum`/`_max` trio rather than one metric, the trailing comma inside
  the label braces, and Java's `5.1380224E8` on a large gauge. The parser handles
  all four already, which is worth having proved rather than assumed.

  *Verified:* `pytest signalforge/tests/adapters -q` - 237 passed. Full suite
  1273 passed, 5 skipped, 0 failed.

- **[S9-T01][REQ-031] Three translation decisions in the health adapter**, each
  of which is a way an adapter could quietly decide something.

  1. **The aggregate status is carried through, never recomputed.** The test
     document says `UP` while its `diskSpace` component says `DOWN`, and the
     report says UP. That is the application's verdict about itself - a Spring
     health group may exclude an indicator on purpose - and an adapter that
     recomputed it would be deciding, which is the SPI's first prohibition.
  2. **An unrecognised status is UNKNOWN, not a guess.** `UP`, `DOWN` and
     `OUT_OF_SERVICE` map to up, down and degraded. Spring's status is an open
     set and an application may register its own; anything else lands on UNKNOWN,
     which says "not measured". Mapping it to UP would be the one answer capable
     of hiding an outage.
  3. **A document with no `components` is still a valid report.**
     `show-details` is off by default, and then Actuator returns the status and
     nothing else. An adapter that needed the components would report every
     default-configured Boot application as broken on the day it was bound.

  Indicator `details` go through the manifest's redaction policy
  (`actuator-health-details`) and are truncated to 200 characters. Details are
  free text the application chose without knowing it would be stored, shown and
  put in front of a model; datasource and mail indicators are the usual way a
  connection string or a credential arrives, so the policy masks addresses,
  `password`/`secret`/`token`-shaped assignments and URL userinfo. The
  truncation is a truncation, not a drop: a 5,000-character detail becomes 200
  characters, not `null`.

- **[S9-T01][REQ-031] `adapters/probes.py`: one synthetic probe, shared.**
  `run_synthetic_probe(transport, probe, now)`, used by both
  `ActuatorHealthAdapter.run_probe` and `ObserveHealthAdapter.run_probe`.

  A probe is not a translation of anything a target publishes - it is SignalForge
  making a request and timing it. Two health adapters measuring that differently
  would report two latencies for one journey and there would be no way to say
  which one an SLO meant. The behaviour is unchanged and unchanged deliberately:
  a non-2xx is a result and `expected_status` decides; a timeout or an
  unreachable target is a failed result carrying the reason, because a probe that
  raised there would take a journey off the board at exactly the moment the
  journey is broken.

## Not built / deferred

- **Logs, actions, chaos and traces for Actuator - deliberately, permanently.**
  Actuator has no log search endpoint, no action manifest, no fault-injection
  surface and no trace store. There is nothing to translate, so there is no
  adapter: a manifest declaring `search` over `/actuator/loggers` (which
  configures log *levels*) or `execute` over `/actuator/shutdown` (which is one
  unparameterised action with no risk class, no approver roles and no
  idempotency key) would be a promise this platform could not keep. The floor
  that makes remediation safe - a validated action manifest with declared risk,
  approvers and verification steps - has no counterpart in Actuator, and an
  adapter cannot invent one.

  This is stated on the registry screen by omission and in `docs/23` in words:
  bind the actuator pair to see the application today; serve `/observe/*` when
  you want SignalForge to read its logs or execute a remediation on it.

- **Discovery does not probe `/actuator/*`.** `DiscoveryService` probes the
  observe paths and the local monitoring services, and matches manifests by kind
  and by vendor. The two adapters therefore appear as candidates once an
  application is known to serve metrics or health, but nothing yet *finds* a
  Boot application by knocking on `/actuator/health`. That is one entry in
  `domain/onboarding/discovery.py`'s target list and no new code; it was out of
  this task's scope and is the obvious next step.

## Deviations

- **No contract or spec was changed.** `signalforge/contracts/` and `specs/` are
  untouched. The two adapters carry existing REQ ids (REQ-031 for the runtimes,
  REQ-030 for the registry assertions); no requirement was added.
- **`observe/adapters.py` was modified**, against the letter of the task's
  closing instruction and in service of its explicit earlier one: "extract it
  into a shared helper in base.py or a small module rather than copy-pasting, if
  that does not touch the frozen SPI". Extraction means the code stops existing
  twice, so `ObserveHealthAdapter.run_probe` now delegates to
  `probes.run_synthetic_probe`. The change is a pure delegation - the two
  `except` clauses that returned identical `ProbeResult`s are now one, and the
  clock is called at the same point - and the existing observe probe tests
  (including the timeout one) pass unchanged. Reverting that one hunk and letting
  the observe adapter keep its own copy is a two-line change if the instruction
  was meant literally.
- **`ping` raises `AdapterUnavailableError` rather than returning `False` when
  the application is down.** The task asked for `False`. The transport's
  `reachable()` returns True or raises, and the health loop's own table reads
  "ping returned false -> the adapter reached it and it said no". A refused
  connection is not that, and reporting it as `False` would put a sentence on
  the integration health screen that is not true. Both land on the same verdict -
  DEGRADED, then DOWN when sustained - so nothing downstream changes; what the
  exception adds is the reason, in `last_error`. The test asserts the honest
  behaviour and says why in its docstring.

## Tests

- **Added: 15**, all in `signalforge/tests/adapters/test_actuator.py`, against
  `signalforge/tests/adapters/actuator_app.py` - a Starlette application serving
  the two Actuator endpoints and one public entry point, and nothing else. It
  mounts on the existing `ObserveHarness`, so the request line, the status
  handling, the size limit and the manifest timeout are exercised for real over
  an ASGI transport; every call goes through the real API (bind) and the real
  `ConnectorInvoker` (call), so the credential-scope floor and the metrics are
  real too. No new fixtures were invented.

  Covered: both manifests discovered and reported *installed* rather than merely
  available; Micrometer text parsed into canonical series; a label selector
  returning only matching series with their labels intact; `query_range` refused
  twice over (the invoker will not route an undeclared capability, and the
  runtime raises `NotSupportedError` naming `prometheus`); the four status
  mappings; the aggregate carried through while a component disagrees; the
  no-components document; redaction of indicator details; truncation at 200
  characters; a wrong-shaped document raising an `AdapterError` that names
  `/actuator/health`; a probe reporting 200 and 503 as results; a hanging
  endpoint on each adapter yielding `AdapterTimeoutError`; an unreachable
  application; and each adapter pinging the endpoint it actually reads.

  The hang is staged on the harness rather than by sleeping in a handler,
  following the note already in `tests/adapters/conftest.py`: an in-process ASGI
  transport has no socket to time out on, so a sleeping handler would test the
  test rather than the adapter.

- **Changed: 1.** `test_shipped_adapters.py`'s `SHIPPED` set gained the two ids,
  so the "a runtime exists for exactly the built-in adapters" and "every
  capability a manifest promises is implemented" checks now cover them.

  | Gate | Result |
  |---|---|
  | `pytest signalforge/tests/adapters -q` | 237 passed |
  | Full Python suite | **1273 passed, 5 skipped, 0 failed** (17m48s) |
  | `scripts/check_clean.sh` | OK |
  | `grep -ri <app name>` over the new files | nothing |
  | `bash run.sh` then `GET /adapters` | both `built_in` / `installed` / `runnable`, 24 adapters, 0 manifest errors |

## Artifacts updated

| # | Path | State |
|---|---|---|
| 04 | `docs/04-functional-spec.md` | new §2.1.1, and the shipped count corrected from seventeen to nineteen |
| 07 | `docs/07-component-architecture.md` | new §4.1: the adapter families, and how much each asks of the application |
| 20 | `docs/20-RTM.md` | regenerated. Large diff: the committed copy had been generated from an 8-test run and showed 51 of 52 requirements as having no linked tests. It now reads 52 of 52 from the full run. |
| 21 | `docs/21-test-report.md` | regenerated from the same run |
| 23 | `docs/23-user-guide.md` | "Onboard an application" now says a Boot application needs no changes, and says what that path does not give you |
| — | `signalforge/infrastructure/adapters/README.md` | two rows in "What this build ships" |
| 11 | `docs/11-openapi.json`, `docs/11-api-spec.md` | regenerated; byte-identical, as expected - no route changed |

## Security notes

- **No new write surface.** Both adapters are `mode: pull`, declare no mutating
  capability and offer only the `read_only` credential scope. The manifest loader
  enforces all three, so neither could be made to execute anything without a
  manifest change that the domain would reject.
- **`auth.method: none` is a property of the target, not a weakening here.**
  Actuator is unauthenticated by default; the adapters hold no credential
  because there is none to hold. Everything else is unchanged: the binding is
  still tenant-scoped, the config still schema-validated, the call still bounded
  by the manifest's timeout, rate limit and response-size ceiling, and
  `base_url` still refused unless it is an absolute http(s) URL - which is the
  control that stops a config field becoming a local file read.
- **A new redaction policy, not a new exemption.** `actuator-health-details`
  masks addresses, credential-shaped assignments and URL userinfo in indicator
  details before they can reach an event, an evidence record or a prompt. It is
  applied at the adapter boundary, once, like every other.
- **No application name anywhere** in the new code, manifests, tests or docs -
  the reference harness's name included; `check_clean.sh` passes, which is what
  enforces it.

## Known issues and technical debt

1. Discovery does not knock on `/actuator/health` yet (above).
2. There is no live end-to-end run against a real Spring Boot application. The
   reference harness serves `/observe/*`, not `/actuator/*`, and no Boot service
   on this machine exposes Actuator, so the evidence is the test double plus the
   registry route on the booted platform. What is verified is stated; what is not
   is this line.
3. `spring-actuator-metrics` reads whatever Micrometer publishes, which on a
   default Boot application is several hundred series per scrape. `max_rows` is
   20,000 and the response ceiling 2 MiB; a very large registry will hit the
   response ceiling before the row limit and will say so.
4. Carried from Sprint 8: single node, SQLite, Postgres-untested; the gateway key
   still needs rotating by a human.

## Demoable checkpoint

```bash
bash run.sh
# then, with a manage_apps token:
curl -s localhost:8000/adapters -H "Authorization: Bearer $TOKEN" \
  | python -c "import json,sys; print([a for a in json.load(sys.stdin)['adapters'] if a['vendor']=='spring'])"
```

Both read `"availability": "built_in", "status": "installed", "runnable": true`.
Against any Spring Boot application with Micrometer, Settings -> Applications ->
New, base URL, bind those two, press Test connection: metrics and health come
back with no change made to the application.

## Coverage checklist ticks

None. Sprint 8 closed the checklist; this sprint widens one of its lines -
"onboarding uses only public contracts" - to an application that publishes no
SignalForge contract at all.

## Entry criteria for next sprint

Not applicable. The obvious follow-on is one line in
`domain/onboarding/discovery.py`'s target list so the wizard finds a Boot
application by itself.
