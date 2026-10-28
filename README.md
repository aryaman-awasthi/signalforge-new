# SignalForge

SignalForge is an incident command centre for production applications. It watches the
applications you connect, tells you when something is wrong and how sure it is, proposes an
incident, runs the war room while people fix it, keeps an explainable AI picture of the
incident, refuses to let anything risky happen without a person, checks recovery against the
application itself, and keeps a tamper-evident record you can replay afterwards.

Three things to know before anything else:

- **The platform proposes. People decide.** It never declares an incident, never runs a
  high-risk action on its own, never sends a message to a customer, and never closes an
  incident. Each of those is a person pressing something, with a permission check behind it.
- **The AI is an accelerator, not a dependency.** With no model configured, every screen still
  works. Detection, severity, policy decisions, approvals, validation and the audit record are
  identical, and confidence numbers come from the same arithmetic. Only the written
  explanations fall back to templates, and the screen says `degraded` when they do.
- **Any application can be onboarded, with no app-specific code.** An application is connected
  through two public contracts. A build guard (`scripts/check_clean.sh`) fails if the demo
  application's name appears anywhere in the core.

Version 0.2.0, as reported by `GET /health`.

## What it does, in one incident

1. **Watch.** Metrics, health, logs, traces, alerts, deployments and topology arrive through
   adapters. Baselines are learned; anomalies have to hold for three evaluations.
2. **Corroborate and propose.** Two independent signal families have to agree, one of them
   customer-facing, before a severity is proposed. Nothing in the detection code can declare.
3. **Declare and run the room.** A person declares. The war room opens, pages people by blast
   radius with visible countdowns, escalates unanswered pages on its own, and gives late
   joiners a brief.
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
platform still starts and says so. No Docker. No internet connection is needed to run.

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

The banner ends with a `Model:` line that reads either `gateway configured` or
`no key set - deterministic mode, which is supported`. The first only means a key is present;
`make llm-smoke` is what checks that the endpoint, the key and the model ids actually work.
Ctrl-C stops everything.

`reset.sh` deletes only what a run wrote: the `var/` folder (databases, caches, generated keys,
logs) and the demo application's data and logs. It never touches source, specs or drills.
Because it deletes the generated keys, every open browser session is signed out.

## See it work

With `run.sh` running, open a second terminal:

```bash
bash scripts/demo.sh
```

This sets up a demonstration world over the public API only: an organisation, one person per
role, an onboarded application with signals, journeys, topology and SLOs, steady traffic, a
learned baseline, an announced deployment, and then a fault. It stops right before a person
presses Declare, and prints the sign-in details. Open the cockpit, sign in as the incident
commander, and the application card reads `PROPOSED`.

To set the world up without a fault, or to choose a different fault, pass arguments through:

```bash
bash scripts/demo.sh drills/demo-setup.yaml --no-fault
```

```bash
bash scripts/demo.sh drills/demo-setup.yaml --refault --scenario license-expiry
```

The demo application lifts every fault on its own after ten to fifteen minutes. `--refault`
re-arms it without touching the incident record.

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
  Anything a model inferred counts a quarter of a measurement. The next-best-action score is
  `urgency x impact x information gain - risk`, computed before any model is asked. The model
  only writes a sentence beside a number, and that sentence is labelled as written by a model.
- **The model has no tools.** Agents return proposals; the platform writes them. A test reads
  the source of every agent and fails if one gains a client, a repository or a bus. The model
  cannot declare, re-grade, close, execute, approve, page or send.
- **Answers must cite the record.** The copilot may only cite nodes of this incident's graph.
  An answer that cites anything else, or claims to be sure with no citation, is discarded and
  replaced by an insufficient-evidence answer that lists what the record holds.
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
  per-tenant cache that stores answers, never prompts.
- **Failure is visible, not fatal.** A missing key, an unreachable gateway, a timeout, a bad
  answer or a spent budget all land in the same place: the deterministic fallback, a `degraded`
  badge, and one `system.llm.degraded` event per episode.
- **What is sent to a model when a key is configured:** short excerpts of incident text (bridge
  lines, alert and change summaries, hypotheses, draft statements), each capped at 1,200
  characters, with email addresses and card-length numbers masked. That is the only masking:
  people's names in chat lines and the question typed into the copilot are not masked, so do
  not describe it as anonymisation. With no key, nothing leaves the machine.

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
| `LLM_MODEL_GENERAL`, `LLM_MODEL_REASONING`, `LLM_MODEL_EMBEDDINGS` | see `.env.example` | Which model serves each class of call. No model id is named in code |
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
hides a key you exported. If you change `SIGNALFORGE_PORT`, also set `SIGNALFORGE_API_URL` and
`NEXT_PUBLIC_API_URL` so the cockpit finds the API.

Set the three `LLM_MODEL_*` values as `.env.example` shows and run `make llm-smoke` before a
live demo. It checks the endpoint, the key and the model ids, and never prints the key. It is
the only thing in the repository that talks to a live model; `make test` never does.

## How it is built

One process runs the API and the background worker: thirteen scheduled jobs (dispatcher,
pollers, detection, probes, escalation sweep, bridge harvest, brain pass, approval expiry,
uncertain-execution watch, ITSM sync, retention). None of them declares, approves, executes,
sends or closes anything. The one machine move inside an incident is recovering back to active
after a recovery validation fails, and a person has to ask for that validation. The cockpit is
a separate Next.js app that talks to the API through a same-origin proxy, so session
tokens live in httpOnly cookies and never reach browser JavaScript. The demo application is a
Spring Boot service that exposes the `/observe/*` contract and pushes changes through
`/api/v1/ingest/*`.

```
signalforge/
  contracts/        frozen public shapes: canonical models, event envelope, /observe and
                    /ingest contracts, manifest schemas, the role matrix
  domain/           the rules: detection, lifecycle, war room, brain, governance, identity
  application/      use cases that drive the domain and write events and audit entries
  infrastructure/   adapters, persistence (SQLAlchemy, Alembic), LLM provider, security
  api/              FastAPI routers and schemas; every route carries its own guard
  workers/          the scheduled jobs
  tests/            1,317 tests; none needs a network or a model
web/                the cockpit (Next.js 14, React 18, TypeScript, Tailwind)
specs/              requirements.yaml (52 requirements) and controls.yaml (26 controls)
drills/             data files that replay a whole incident; never mutated by a run
reference-app/      the demo application (Java 17); delete it and the product still runs
docs/               the numbered artefacts, screenshots and sprint logs
scripts/            launchers, generators, the drill runner, the evidence tool
```

Data lives in two SQLite files under `var/`: the main database (identity, incidents, graph,
ledger, events, audit chain, encrypted secrets, model cache) and a prunable rollup store. Every
state change is written together with its event in one transaction; the audit log is a
per-tenant hash chain with no off switch. Adapters are folders with a `manifest.yaml`; thirteen
kinds exist (metrics, logs, traces, health, alerts, changes, topology, collaboration, ITSM,
actions, notifications, knowledge, chaos).

## Tests and evidence

```bash
make test
```

The suite is 1,317 tests (8 skip without a model key or Java). It runs against temporary SQLite
files with a fake clock. Some useful subsets:

| Command | What it proves |
|---|---|
| `python -m pytest signalforge/tests/ai_eval -q` | the eighteen AI controls, in about a second |
| `python -m pytest signalforge/tests/security -q` | tenant isolation, RBAC, HMAC, audit chain, AI boundaries |
| `python -m pytest signalforge/tests/drills -q` | a whole incident replayed on a virtual clock, no model, no network |
| `python scripts/demo_check.py` | the tests behind the twelve demonstration checks |
| `make clean-check` | the core names no application |
| `cd web && npm run typecheck` | the cockpit compiles |

Every test carries a requirement id. `make rtm` regenerates the traceability matrix
(`docs/20-RTM.md`) and `make report` the test report (`docs/21-test-report.md`) from the last
run. `make evidence REQ=REQ-062` prints one requirement's text, the files that implement it,
its tests with their last result, and the events the running platform has emitted for it.

## Documentation

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
  helpfulness or robustness under a red-team campaign. Only two opt-in tests ever talk to a real
  model.
- Redaction before a prompt masks email addresses and card-length numbers only.
- The audit chain is tamper-evident, not tamper-proof. It detects edited, re-hashed or deleted
  entries, but it is not signed or anchored outside the database, so someone with write access
  to the database who recomputed every later hash would not be caught. Seals (`POST /seals`)
  give you a hash to copy somewhere else.
- Sign-in is local accounts only: passwords plus invitation links. There is no single sign-on
  in this build.
- `observability/` is a placeholder. There is no Prometheus or Grafana provisioning in this
  repository, although `/metrics` serves Prometheus text.
- The web dependencies carry four known high-severity advisories (in `next`, `postcss` and the
  Playwright dev tools), accepted for this release with the upgrade scheduled. The Python
  dependency audit did not run on the build machine.
- `docs/20-RTM.md` and `docs/21-test-report.md` were last generated from a September run and
  should be regenerated before the next release.
- The sprint-11 log lists smaller items noted and not fixed, such as a race between two
  commanders declaring at once and times shown without their zone.

## Conventions

- Commits are tagged with the sprint task and the requirements they serve, for example
  `[S11-T07][REQ-012, REQ-013]`, and a fix carries a test that failed before it.
- `signalforge/contracts/` is frozen: referenced, never redefined, changed only on an explicit
  instruction quoted in the commit.
- The demo application's name must not appear outside `reference-app/`, `drills/`, `seeds/`,
  the tests and its own config folder. `make clean-check` enforces this.
- `.env`, `var/` and generated keys are never committed.
