# PaySphere: the reference harness

PaySphere is a small payments application. It exists for one reason: to be the
thing SignalForge is onboarded against, so that "any application can be onboarded
through public contracts" is a claim somebody can check rather than a slogan.

It is **demo data, not product** (CLAUDE.md rule 7). Delete `reference-app/` and
the platform still builds, boots and passes its suite; the only thing lost is the
evidence. Nothing under `signalforge/` names this application, and
`scripts/check_clean.sh` fails the build if that ever stops being true.

## What makes it a fair test

The harness has no SignalForge dependency, no SDK, no generated client and no
shared package. Grep the sources: every occurrence of "SignalForge" is a comment
or the `X-SignalForge-Synthetic` request header the platform asks applications to
honour. The coupling in each direction is a wire format:

| Direction | Contract | Where it is frozen |
|---|---|---|
| SignalForge reads the app | `GET/POST /observe/*` | `signalforge/contracts/observe.py` |
| The app declares its actions | action manifest JSON | `signalforge/contracts/schemas/action-manifest.schema.json` |
| The app declares its faults | chaos manifest JSON | `signalforge/contracts/schemas/chaos-manifest.schema.json` |
| The app's pipeline tells SignalForge | `POST /api/v1/ingest/changes` | `signalforge/contracts/ingest.py` |

`signalforge/tests/reference/` holds the tests that keep those four honest,
including one that starts this jar and onboards it end to end.

## Running it

```bash
bash reference-app/paysphere/run.sh          # builds if stale, then starts on :8080
```

Environment (all optional):

| Variable | Default | Meaning |
|---|---|---|
| `PAYSPHERE_PORT` | 8080 | HTTP port; `0` picks a free one |
| `PAYSPHERE_DATA_DIR` | `./data` | H2 database directory |
| `PAYSPHERE_LOG_DIR` | `./logs` | JSONL log directory |
| `PAYSPHERE_TRAFFICGEN` | true | seeded payment traffic |
| `PAYSPHERE_KEY_READ_ONLY` | `ps-ro-…` | credential for telemetry |
| `PAYSPHERE_KEY_REMEDIATION` | `ps-rem-…` | credential for actions |
| `PAYSPHERE_KEY_DRILL` | `ps-drill-…` | credential for chaos |
| `PAYSPHERE_KEY_DEPLOY` | `ps-deploy-…` | credential for the config pipeline |

The default keys are in `application.yml` in plain sight, because they guard a
demo harness and pretending otherwise would be theatre. Set the environment
variables for anything that is not a demo.

Requires Java 17 or newer and Maven. Behind a TLS-inspecting proxy Maven needs a
truststore containing the proxy's CA:

```bash
keytool -importcert -keystore my-truststore.jks -file proxy-ca.cer -alias proxy
export MAVEN_OPTS="-Djavax.net.ssl.trustStore=$PWD/my-truststore.jks -Djavax.net.ssl.trustStorePassword=changeit"
```

## What is inside

| Package | What it is |
|---|---|
| `payments` | the four journeys (upi, card, wallet, refund) and the public API probes exercise |
| `ledger` | H2 tables: payments, settlements, **executions**, chaos injections, config versions |
| `fabric` | seven links (GW-A, GW-B, AGG-A, AGG-B, FIB-7, CORE, RAIL), a route per region, injectable latency/loss/down |
| `runtimeconfig` | versioned per-region configuration, the deploy endpoint, the injectable apply delay |
| `observe` | the twelve frozen observe paths, a hand-rolled Prometheus registry, the log ring and JSONL file, the span buffer, health and topology |
| `actions` | the published action manifest and its seven implementations |
| `chaos` | the published chaos manifest, its five faults, expiry and abort |
| `trafficgen` | a seeded generator placing payments in both regions |
| `security` | four bearer credentials and the scope rules between them |

### Three things worth knowing before reading the code

**A payment lost at the rail is `UNKNOWN`, not `FAILED`.** The request reached the
point where money moves and the answer was lost. Calling that a failure would be
a guess, and a caller told "failed" would retry and pay twice. `UNKNOWN` is what
`run-reconciliation-job` exists to resolve, and it is why the harness can
demonstrate anything at all about uncertain external state.

**An execution row is written before the action is attempted.** The caller's
idempotency key is the primary key of `executions`. So a caller that timed out
can still find out what happened (`GET /observe/actions/executions/{key}`), a
retry under the same key is the same execution rather than a second side effect,
and a harness killed mid-action leaves a row saying `running` - an honest report
of an unknown outcome instead of a missing one.

**The manifests are documents, not a scan of the code.** The harness refuses to
start if a published action has no handler or a handler is not published. That is
stricter than generating the manifest from annotations, and deliberately: a
generated manifest cannot be wrong, which means it also cannot be reviewed.

### The fabric, and why one region breaks

```
region-a  primary:   GW-A -> AGG-A -> CORE -> RAIL
          secondary: GW-A -> AGG-B -> CORE -> RAIL
region-b  primary:   GW-B -> AGG-B -> FIB-7 -> CORE -> RAIL
          secondary: GW-B -> AGG-A -> CORE -> RAIL
```

`FIB-7` is a shared fibre span only region-b's primary route crosses, so damaging
it produces a genuine single-region incident with a healthy control region beside
it - which is what makes blast radius, corroboration and "should we fail over"
into real questions. `RAIL` is on every route, because a payments business cannot
route around the thing that moves the money; a fault there is everyone's problem
and produces `UNKNOWN` payments rather than failures.

`failover-route` moves a region onto its secondary. It is published as a
high-risk `traffic_shift` requiring an approver, so the policy floor refuses to
let anything automate it.

## Credential scopes

| Path | Credential required |
|---|---|
| `/observe/metrics,logs,traces,health,topology` | any of the four |
| `/observe/actions**` | remediation |
| `/observe/chaos**` | drill, and the injection's own `scope` must be `drill:<id>` |
| `/api/deploy/**` | deploy |

A missing or unknown credential is 401; a valid credential with the wrong scope
is 403. SignalForge's auto-discovery reads either as "served, and it wants a
credential", which is exactly what is true.

## The configuration repository

`paysphere-config/` is the application's config repo. Its `deploy.sh` applies a
release and announces it to SignalForge over the ingest contract. See that
directory's README.
