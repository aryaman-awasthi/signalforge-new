# SignalForge

SignalForge is an incident command centre for production applications. It watches the
applications you connect and tells you when something is wrong, and how sure it is. It proposes
an incident; a person declares it. It runs the war room while people fix the problem and keeps
an explainable AI picture of the incident. It refuses to let anything risky happen without a
person. It checks recovery against the application itself. And it keeps a tamper-evident record
you can replay afterwards.

Three things to know before anything else:

- **The platform proposes. People decide.** It never declares an incident, never runs a
  high-risk action on its own, never sends a message to a customer, and never closes an
  incident. Each of those is a person pressing something, with a permission check behind it.
- **The AI is an accelerator, not a dependency.** With no model configured, every screen still
  works. Detection, severity, policy decisions, approvals, validation and the audit record are
  identical, and confidence numbers come from the same arithmetic. Only the written
  explanations fall back to templates, and the AI panels say `degraded` when they do.
- **Any application can be onboarded, with no app-specific code.** An application is connected
  through two public contracts. A build guard (`scripts/check_clean.sh`) fails if the demo
  application's name appears anywhere in the core.

Version 0.2.0, as reported by `GET /health`. (The package metadata in `pyproject.toml` and
`web/package.json` still says 0.0.1; `/health` is the number to quote.)

## What it does, in one incident

1. **Watch.** Metrics, health, logs, traces, alerts, deployments and topology arrive through
   adapters. Baselines are learned per application; a breach has to hold for three evaluations
   in a row before a signal reads `anomalous`.
2. **Corroborate and propose.** Two independent signal families have to agree, one of them
   customer-facing, before a severity is proposed. Nothing in the detection code can declare.
3. **Declare and run the room.** A person declares. The war room opens, pages people by blast
   radius (the services and people the fault reaches) with visible countdowns, escalates
   unanswered pages on its own, and gives late joiners a brief.
4. **Reason.** The incident brain keeps competing hypotheses with confidence numbers that are
   arithmetic over weighted evidence, a history of every move, and the one test that would
   settle each. A copilot answers only from the incident's own record, with citations.
5. **Act, under a floor.** Next-best-action ranks the application's own actions with a formula
   you can check by hand. High-risk actions can never auto-execute; a second person approves;
   an action whose outcome is unknown is marked `uncertain` and cannot be retried until the
   application has been asked what happened.
6. **Recover and communicate.** Recovery is validated on four layers read from the application,
   never from the AI. Customer statements must cite the record and need approval. Nothing is
   ever sent automatically.
7. **Prove it.** Every act is in a hash-chained audit log. An auditor replays the incident, the
   chain is verified, and a post-incident review refuses to generate from a broken chain.

## Quick start

You need Python 3.11 or newer (tested on 3.13) and Node.js 20 or newer with npm. Java 17 and
Maven are needed only for the demo application under `reference-app/`; without them the
platform still starts and says so. No Docker. The two install steps below need an internet
connection; running does not.

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

```bash
cd web && npm install && cd ..
```

```bash
cp .env.example .env
```

The copy of `.env` is optional. Every value in it is optional too: the platform boots with
none of them set, generates the two keys it cannot invent into `var/keys/` on first start, and
runs its AI surfaces in deterministic mode until a model key is configured.

```bash
bash reset.sh
```

```bash
bash run.sh
```

On Windows, use `.venv\Scripts\pip` for the install, then `reset.cmd` and `run.cmd` from
cmd.exe (or `.\reset.ps1` and `.\run.ps1` from PowerShell). They behave the same way.

In the commands below, `python` means the interpreter in `.venv` (activate it, or write
`.venv/bin/python`).

`run.sh` applies the database migrations, starts the demo application if it can be built,
starts the API and the web cockpit, and reports each part as up only after it has answered a
real HTTP request. If something essential does not come up it exits non-zero and names the log
file under `var/run/`. When everything is up it prints:

| What | Where |
|---|---|
| Cockpit (web) | http://127.0.0.1:3000 |
| API | http://127.0.0.1:8000 |
| API documentation | http://127.0.0.1:8000/docs |
| Health | http://127.0.0.1:8000/health |
| Metrics (Prometheus text) | http://127.0.0.1:8000/metrics |
| Demo application | http://127.0.0.1:8080 (only when it started) |

The banner has a `Model:` line that reads either `gateway configured` or
`no key set - deterministic mode, which is supported`. The first only means a key is present;
`make llm-smoke` is what checks that the endpoint, the key and the model ids actually work.
Ctrl-C stops everything.

`run.sh` starts the cockpit with the Next.js development server, not a production build, and
the API speaks plain HTTP on 127.0.0.1. A real deployment puts a reverse proxy in front of the
API to terminate TLS (`docs/09-deployment.md`).

`reset.sh` deletes only what a run wrote: the `var/` folder (databases, caches, generated keys,
logs, the test recording) and the demo application's data and logs. It never touches source,
specs or drills. Because it deletes the generated keys, every open browser session is signed
out.

## See it work

With `run.sh` running, open a second terminal:

```bash
bash scripts/demo.sh
```

This sets up a demonstration world over HTTP, through the platform's public API and the demo
application's own endpoints: an organisation, a person for every role (seven people plus the
owner, two of them SMEs), an onboarded application with signals, journeys, topology and SLOs
(service level objectives), steady traffic, a learned baseline, an announced deployment, and
then a fault. It stops right before a person presses Declare, and prints the sign-in details as
JSON. Sign-in needs three fields: the organisation slug from that JSON, an email such as
`ic@demo.test`, and the password `drill-strong-password`. Signed in as the incident commander,
the application card reads `PROPOSED`.

To set the world up without a fault, or to choose a different fault, pass arguments through:

```bash
bash scripts/demo.sh drills/demo-setup.yaml --no-fault
```

```bash
bash scripts/demo.sh drills/demo-setup.yaml --refault --scenario license-expiry
```

The demo application lifts every fault on its own after five to fifteen minutes, depending on
the fault; `license-expiry` lasts fifteen. `--refault` injects again without touching the
incident record, but it does not extend a fault that is still running: each injection has its
own clock. To stop a fault early, open the demo application's console at
http://127.0.0.1:8080 and press `abort` on the injection.

Two scripted versions of the demo exist:

- `docs/demo-script.md`: the twelve-check demonstration, with `python scripts/demo_check.py`
  running the tests behind every check.
- `docs/demo-script-ai-lead.md`: a 30-minute walkthrough written for an AI leader, with the
  questions they are likely to ask and the honest answers.

## Who can do what

The role matrix is frozen in `signalforge/contracts/roles.py`. Roles are held by people only;
the AI has no role.

| Role | Can |
|---|---|
| Org Owner | manage the organisation, applications, policies and integrations; run drills; read the audit |
| Incident Commander | declare, change severity, approve high-risk actions, close, run drills |
| Tech Lead | declare, change severity, approve high-risk actions |
| SME | investigate and answer pages. Holds no permissions at all, on purpose |
| Comms Lead | approve customer communications |
| Executive | approve executive communications, and nothing operational |
| Auditor | read the record. Nothing else |

The SME holding no permissions is a design choice: answering a page, joining the room, speaking
on the bridge and ticking your own task are tied to your own session, not to a permission, so
nobody can do them on your behalf.

## How the AI is used, and where it stops

- **The model never produces a number.** Hypothesis confidence is arithmetic over evidence the
  platform holds, with a published weight table (`signalforge/domain/brain/confidence.py`).
  A record from a system of record counts fully, a measurement counts 0.9, and anything a model
  inferred counts a quarter. The next-best-action score is
  `urgency x impact x information gain - risk`, computed before any model is asked. The model
  only writes a sentence beside a number, and that sentence is labelled as written by a model.
- **The model has no tools.** Agents return proposals; the platform writes them. A test reads
  the source of every agent and fails if one gains a client, a repository or a bus. The model
  cannot declare, re-grade, close, execute, approve, page or send.
- **Answers must cite the record.** The copilot may only cite nodes of this incident's graph.
  An answer that cites anything else, or claims to be sure with no citation, is discarded and
  replaced by an insufficient-evidence answer that lists what the record holds. The check is
  structural: it proves the citation exists, not that the sentence reads the record faithfully.
- **Model traffic can go to two hosts, fixed in code.** `genailab.tcs.in` (the TCS GenAI Lab
  gateway) and `api.anthropic.com` (the Claude API), over HTTPS only, with TLS verification
  that no setting can switch off. Any other endpoint is treated as not configured: no client is
  built and nothing is sent. The list lives in `signalforge/infrastructure/llm/hosts.py`.
- **Untrusted text is fenced.** Chat lines, logs and tickets reach the model inside a marked
  data block, under a standing instruction that data is never an instruction. Injection
  attempts are flagged on the timeline and kept as evidence; nothing happens, because there is
  nothing for the model to press.
- **Every answer is checked against a schema.** Off-schema output is treated as no answer.
  Every call has a timeout (20 s by default, 120 s at most). Each incident has a token budget
  (200,000 by default) checked before a call leaves. Identical requests are answered from a
  per-tenant cache that stores answers, never prompts; copilot questions are never cached.
- **Failure is visible, not fatal.** A missing key, an unreachable gateway, a timeout, a bad
  answer or a spent budget all land in the same place: the deterministic fallback, a `degraded`
  badge, and one `system.llm.degraded` event per episode.
- **What is sent to a model when a key is configured:** short excerpts of incident text (bridge
  lines, alert and change summaries, hypotheses), each capped at 1,200 characters, plus the
  copilot question (up to 500 characters) and the draft statement being rewritten (up to 4,000
  characters). Email addresses and card-length numbers are masked. That is the only masking:
  people's names in chat lines and the copilot question are not masked, so do not describe it
  as anonymisation. With no key, nothing leaves the machine.

The eighteen AI controls are tested offline in `signalforge/tests/ai_eval/` and described in
`docs/15-ai-eval.md`. That suite is a regression fence, not a benchmark, and it does not
measure accuracy in the field.

## Configuration

Configuration is read from environment variables. The launch scripts (`run.sh`, `reset.sh`,
`scripts/demo.sh`, `make llm-smoke` and their Windows twins) load `.env` for you; the Python
code itself reads only the process environment. `.env.example` is the commented template and
carries no secret values. The ones most people touch:

| Variable | Default | What it does |
|---|---|---|
| `GENAILAB_BASE_URL` | `https://genailab.tcs.in/v1` | Model endpoint. Must be one of the two approved hosts or the model path stays off |
| `GENAILAB_API_KEY` | unset | Model key. Unset means deterministic mode |
| `LLM_MODEL_GENERAL`, `LLM_MODEL_REASONING`, `LLM_MODEL_EMBEDDINGS` | three fallback ids in `signalforge/infrastructure/config.py` | Which model serves each class of call. Set all three as `.env.example` shows: the built-in fallbacks are gpt-5.4 ids the template warns may reject temperature zero. No domain or application code names a model, and a test checks that |
| `LLM_TIMEOUT_S` | `20` | Per-call timeout, capped at 120 |
| `LLM_INCIDENT_TOKEN_BUDGET` | `200000` | Token cap per incident |
| `LLM_CACHE` | `true` | Answer cache, per tenant |
| `LLM_CA_BUNDLE` | unset | Extra CA certificate to trust. There is no variable that disables TLS verification |
| `SIGNALFORGE_PORT`, `SIGNALFORGE_WEB_PORT`, `SIGNALFORGE_HARNESS_PORT` | `8000`, `3000`, `8080` | Ports used by `run.sh` |
| `SIGNALFORGE_DB_URL` | SQLite file under `var/` | Main database. Postgres-ready, not yet tested on Postgres |
| `SIGNALFORGE_JWT_SECRET`, `SIGNALFORGE_FERNET_KEY` | generated into `var/keys/` | Set them explicitly in any real deployment and back up the Fernet key |
| `SIGNALFORGE_WORKER_ENABLED` | `1` | `0` makes a node serve requests only |
| `SIGNALFORGE_SIGNAL_RETENTION_DAYS` | `7` | How long raw signal events are kept (1 to 90) |

Two things to know about `.env`: the scripts load it with `set -a`, so a value in `.env` wins
over the same variable exported in your shell, and an empty `GENAILAB_API_KEY=` line in `.env`
hides a key you exported. If you change `SIGNALFORGE_PORT`, also set `SIGNALFORGE_API_URL`,
`NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_WS_URL` so the cockpit finds the API and its live
socket, and `SIGNALFORGE_API` so `scripts/demo.sh` finds it. The API accepts browser calls only
from port 3000 (the CORS list in `signalforge/api/main.py`), so leave `SIGNALFORGE_WEB_PORT`
alone unless you change that list too.

Set the three `LLM_MODEL_*` values as `.env.example` shows and run `make llm-smoke` before a
live demo. It checks the endpoint, the key and the model ids, and never prints the key. On the
GenAI Lab gateway it exits 0 when everything works. On the Claude endpoint it exits 1 even when
chat works, because that endpoint has no embeddings; read its `chat` line there. It is the only
script in the repository that talks to a live model. `make test` never does unless you set both
`GENAILAB_API_KEY` and `SIGNALFORGE_LIVE_LLM=1`, which turns on five opt-in live tests.

## How it is built

One process runs the API and the background worker: thirteen scheduled jobs (dispatcher,
connector health, metric rollups, detection, probes, rollup retention, page escalation, bridge
harvest, brain pass, approval expiry, uncertain-execution watch, ITSM sync, event retention).
None of them declares, approves, executes, closes, or sends a customer communication. The
escalation sweep does page the next responder when a page goes unanswered; that is the one
thing a job sends. No job changes an incident's state. The one automatic move inside an
incident, recovering back to active, happens inside a recovery validation that a person asked
for and that then failed.

The cockpit is a separate Next.js app that talks to the API through a same-origin proxy, so
session tokens live in httpOnly cookies and never reach browser JavaScript. The demo
application is a Spring Boot service that exposes the `/observe/*` contract; it never calls the
platform. Its deployment pipeline, the small config repository at the root of this checkout,
announces each deploy through `/api/v1/ingest/changes` with a signed request.

```
signalforge/
  contracts/        frozen public shapes: canonical models, event envelope, /observe and
                    /ingest contracts, manifest schemas, the role matrix
  domain/           the rules: detection, lifecycle, war room, brain, governance, identity
  application/      use cases that drive the domain and write events and audit entries
  infrastructure/   adapters, persistence (SQLAlchemy, Alembic), LLM provider, security
  api/              FastAPI routers and schemas; every route except health, metrics and
                    sign-in needs a session, and each names its own permission
  workers/          the scheduled jobs
  tests/            1,317 tests
web/                the cockpit (Next.js 14, React 18, TypeScript, Tailwind)
specs/              requirements.yaml (52 requirements) and controls.yaml (26 controls)
drills/             data files that replay a whole incident; never mutated by a run
seeds/              empty by design; drills and seeds are data a run consumes
reference-app/      the demo application (Java 17); delete it and the product still runs
<name>-config/      the demo application's deploy pipeline: a deploy.sh that signs a change
                    event and posts it to the ingest contract
observability/      a placeholder; nothing is provisioned here yet
docs/               the numbered artefacts, screenshots and sprint logs
scripts/            launchers, generators, the drill runner, the evidence tool
```

Data lives in two SQLite files under `var/`: the main database (identity, incidents, graph,
ledger, events, audit chain, encrypted secrets, model cache) and a prunable rollup store. Every
state change is written together with its event in one transaction; the audit log is a
per-tenant hash chain with no off switch. Adapters are folders with a `manifest.yaml`; thirteen
kinds exist (metrics, logs, traces, health, alerts, changes, topology, collaboration, ITSM
ticketing, actions, notifications, knowledge, chaos). The Datadog, Splunk, ServiceNow, GitHub
and Jenkins entries in the adapter list are interface manifests with no runtime in this build.

Code comments and some documents cite `CLAUDE.md rule N` and a `playbook/` folder. Both are
working files that are deliberately not committed, so you will not find them in the checkout.

## Tests and evidence

```bash
make test
```

The suite is 1,317 tests, run against temporary SQLite files with a fake clock. It passes with
no network and no model. Eight tests skip by default: five live-model tests that run only with
`GENAILAB_API_KEY` and `SIGNALFORGE_LIVE_LLM=1` set, and three that need the demo
application's jar built. Some useful subsets:

| Command | What it proves |
|---|---|
| `python -m pytest signalforge/tests/ai_eval -q` | the eighteen AI controls, in about a second (22 items; one control runs five payloads) |
| `python -m pytest signalforge/tests/security -q` | tenant isolation, RBAC, HMAC, audit chain, AI boundaries |
| `python -m pytest signalforge/tests/drills -q` | a whole incident replayed on a virtual clock, no model, no network |
| `python scripts/demo_check.py` | the tests behind the twelve demonstration checks |
| `make clean-check` | the core names no application |
| `cd web && npm run typecheck` | the cockpit compiles |

Every test carries a requirement id, and every pytest run writes its outcomes to
`var/rtm/test-results.json` (which `reset.sh` deletes). `make rtm` regenerates the
traceability matrix (`docs/20-RTM.md`) and `make report` the test report
(`docs/21-test-report.md`) from that recording. `make evidence REQ=REQ-062` prints one
requirement's text, the files that implement it, its tests with their result from the last
recorded run, and the events the running platform has emitted for it. Run `make test` once
after a reset before trusting the test column.

## Documentation

Where a document below and the code disagree, the code and this README are current. Known
stale spots: `docs/06` still names LangGraph and APScheduler (the governor is plain Python, see
`docs/decision-record.md` DR-100); `docs/09`, `docs/12` and `docs/19` describe one approved
model host where the code has two; `docs/09` and `docs/22` describe Prometheus and Grafana
provisioning that is not in the repository; `docs/20` and `docs/21` count an older run.

| Document | What it covers |
|---|---|
| `docs/01-BRD.md` | the problem, the product, goals and non-goals |
| `docs/04-functional-spec.md` | what each part does |
| `docs/06-solution-architecture.md`, `docs/07-component-architecture.md` | how it is built |
| `docs/08-agent-architecture.md` | the AI: governor, agents, budgets, boundaries |
| `docs/09-deployment.md` | processes, ports, configuration, Postgres path, limits |
| `docs/11-api-spec.md`, `docs/11-openapi.json` | every route, its credential and permission |
| `docs/12-threat-model.md`, `docs/13-controls-matrix.md`, `docs/19-vulnerability-assessment.md` | security |
| `docs/15-ai-eval.md` | the eighteen AI controls and what the suite does not claim |
| `docs/20-RTM.md`, `docs/21-test-report.md` | generated traceability and test report |
| `docs/23-user-guide.md`, `docs/24-onboarding-guide.md` | using it and onboarding an application |
| `docs/demo-script.md`, `docs/demo-script-ai-lead.md` | demonstrations |
| `docs/sprint-logs/` | what each sprint built, deferred and left open |

## Known limitations

- One machine, one process, SQLite. The code is Postgres-ready but has not been tested on
  Postgres. The token budget ledger, the login rate limiter and the brain's wake set are held in
  process memory and reset on restart.
- The chat, paging and ticket channels in the demo are simulated adapters, badged `SIMULATED` on
  screen. No real Teams, pager or ITSM vendor is connected in this build.
- The AI evaluation suite is eighteen controls over fixed inputs. It does not measure accuracy,
  helpfulness or robustness under a red-team campaign. Only five opt-in tests ever talk to a
  real model.
- Redaction before a prompt masks email addresses and card-length numbers only.
- The audit chain is tamper-evident, not tamper-proof. It detects edited, re-hashed or deleted
  entries, but it is not signed or anchored outside the database, so someone with write access
  to the database who recomputed every later hash would not be caught. Seals (`POST /seals`)
  give you a hash to copy somewhere else.
- Sign-in is local accounts only: passwords plus invitation links. There is no single sign-on
  in this build.
- `observability/` is a placeholder. There is no Prometheus or Grafana provisioning in this
  repository, although `/metrics` serves Prometheus text.
- The web dependencies carry four known high-severity advisories (two in the `next` build, two
  in the Playwright test tooling), accepted for this release with the upgrade scheduled. The
  Python dependency audit did not run on the build machine.
- Drills are hand-written data files; the planned drill-authoring agent is not built.
- The demo application's four bearer credentials are demo keys printed in its own configuration
  on purpose. They guard a demo, not a product.
- `docs/20-RTM.md` and `docs/21-test-report.md` were last generated from a September run and
  should be regenerated before the next release.
- The sprint-11 log lists smaller items noted and not fixed, such as a race between two
  commanders declaring at once and times shown without their zone.

## Conventions

- Commits are tagged with the sprint task and the requirements they serve, for example
  `[S11-T07][REQ-012, REQ-013, REQ-033, REQ-045]`, and a fix carries a test that failed before
  it.
- `signalforge/contracts/` is frozen: referenced, never redefined, changed only on an explicit
  instruction quoted in the commit.
- The demo application's name must not appear outside `reference-app/`, `drills/`, `seeds/`,
  the tests and its own config folder. `make clean-check` enforces this.
- `.env`, `var/` and generated keys are never committed.
