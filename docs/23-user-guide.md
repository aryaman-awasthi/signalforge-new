# 23 - User and operator guide

Owner: engineering. Sprint 7. Status: complete.

SignalForge is an incident command centre. It watches applications you onboard
through public contracts, tells you when something is wrong and how sure it is,
runs the room while you fix it, refuses to let anything dangerous happen without
a person, and keeps a record that can be checked afterwards.

Two things are worth knowing before anything else:

- **The platform proposes; people decide.** It never declares an incident, never
  executes a high-risk action on its own, never sends a communication, and never
  closes an incident. Every one of those is a person pressing something.
- **The AI is an accelerator, never a dependency.** With no model configured,
  every screen still works and says `degraded`. Nothing in the critical path
  needs a gateway to be up.

---

## Part 1 - Getting started

### Start it

```bash
bash reset.sh    # back to a clean baseline
bash run.sh      # migrate, start everything, wait, print the URLs
```

The cockpit is at `http://127.0.0.1:3000`, the API at `:8000`, its
documentation at `:8000/docs`.

### Sign up and invite your team

The first person to sign up creates the organisation and becomes its Org Owner.
Everybody else arrives by invitation with roles attached.

| Role | Can |
|---|---|
| **Org Owner** | manage the org, apps, policies and integrations; read the audit |
| **Incident Commander** | declare, change severity, approve high-risk actions, close |
| **Tech Lead** | declare, change severity, approve high-risk actions |
| **SME** | investigate and answer pages - deliberately holds no permissions |
| **Comms Lead** | approve customer communications |
| **Executive** | approve executive communications, and nothing operational |
| **Auditor** | read the record. Nothing else, ever. |

That the SME role holds no permissions is intentional. Answering a page is
self-scoped: the page names its recipient and the session names itself, so
acknowledging somebody else's page is refused by the entity whatever roles you
hold.

### Onboard an application

Settings → Applications → New, then walk the wizard:

1. **Connect** - bind adapters (metrics, health, logs, actions, chaos,
   notifications, collaboration). Credentials are encrypted at rest.
2. **Test** - run a real connection test. A failure here is a normal outcome and
   the wizard shows you what the application answered.
3. **Discover** - the platform reads what the application publishes.
4. **Map** - golden signals, the region label, journeys, probes, and the actions
   from the application's own manifest with their risk classes.
5. **Topology** - upload a version; later versions produce a delta.
6. **SLOs and severity** - objectives and the severity matrix.
7. **Baseline and readiness** - let a baseline form, then read the score.

Your application needs to serve twelve `/observe/*` paths and, if it wants to
push, speak the ingest contract with an HMAC. That is the whole integration
requirement; `docs/11-api-spec.md` has the exact shapes.

**A Spring Boot application can be onboarded with no changes at all.** If it has
Micrometer on its classpath - most Boot services do - it is already serving
`/actuator/prometheus` and `/actuator/health`. Bind `spring-actuator-metrics`
and `spring-actuator-health` at step 1 with the application's base URL (or its
management port, if management runs on one) and no credential: Actuator is
unauthenticated by default, so those two adapters ask for none. You then get
metrics, the health of every indicator the application registers, and synthetic
probes, which is enough for detection, the golden signals and journeys.

What you do *not* get that way is logs, actions and chaos. Actuator has no log
search, no action manifest and no fault-injection surface, so no adapter can
offer them honestly - those still require the `/observe` contract. Bind the
actuator pair to start seeing the application today, and serve `/observe/*` when
you want SignalForge to read its logs or execute a remediation on it.

---

## Part 2 - Running an incident

### When something is wrong

The application page shows a **stance**: Steady, Watch, or Proposed. Proposed
means the platform believes there is an incident and has computed a severity from
measured impact - but it has **declared nothing**. Read the proposal: it names
the signal families that corroborate it, whether one is customer-facing, how many
regions are affected and the journey tier.

**You declare.** Nothing else can.

### The war room

Declaring opens a room, pages by blast radius, and starts countdowns. The ack
board shows every page with the time left to answer; an unanswered page escalates
to the next person in the rotation on its own. Late arrivals read the **join
brief** rather than scrolling.

Commitments made in the room ("I'll check the pool") become tasks with owners
and due times.

### Reasoning

The **Incident Brain** panel holds what the platform believes, versioned, with
provenance on every node. Hypotheses carry:

- a confidence, computed deterministically,
- the arithmetic that produced it,
- the evidence for and against,
- and the **discriminating test** - the one thing that would move it most.

Say what you measure in the room. The brain re-reasons over it. If somebody
claims a component is healthy while telemetry says it is not, that contradiction
is raised rather than averaged away.

Blast radius is always two lists: **CONFIRMED** (measured) and **PREDICTED**
(inferred from topology). They are never merged.

### Acting on it

**Next Best Action** ranks what to do, with the score broken into its terms
(urgency, impact, information gain, minus risk) and an alternative and a
fallback beside it.

When you request a high-risk action:

1. Policy decides, and shows which rules it hit.
2. If approval is required, a **different person** must grant it. The domain
   refuses self-approval whatever permissions you hold.
3. The Tool Gateway sends it once, under a derived idempotency key.

**If the target does not answer, the action lands `UNCERTAIN`.** This is the most
important state on the platform. It does not mean the action failed - it means
nobody knows. Retry is refused until you **reconcile**: the platform asks the
application what actually happened. The answer is `COMPLETED_LATE`, `NOT_APPLIED`
or `PARTIAL`, and only then does the ledger let you act again.

### Communications

Drafts are grounded on graph nodes and cite them. Nothing sends itself: a
communication is drafted, approved by somebody other than its author for
external audiences, and then issued by a person.

If the evidence a published statement was written from is later superseded, the
statement is flagged **contradicted** - and its text is left exactly as
published. Correcting it is a new communication with a redline and its own
approval.

### Closing

Recovery is validated by four independent layers - technical, service, customer,
business - that read the application directly and never ask the brain. **The
customer layer blocks closure.** Technical green while customers still cannot pay
is not recovery, and the platform will refuse the close with a reason.

Once the layers pass and the stability window holds, you close it. A person, again.

---

## Part 3 - Afterwards

### Replay

`GET /incidents/{id}/replay`, available to anybody holding `read_audit` -
which for the Auditor role is the only thing they hold - reconstructs the
incident from the hash-chained record: every line with when it happened, who or
what caused it, what justified it, what changed, and references you can open.
`?until_seq=` is the scrubber: it returns the incident as it stood at that point,
through the same code path, so a partial view cannot disagree with the whole.

The screen is `/incidents/{id}/replay`, linked from the war-room header once an
incident is closed and at any time for anybody holding `read_audit`. The
integrity banner is the first thing on it, because if the chain does not verify
then every line below it is suspect and you should be told that before you read
them. The scrubber is the `?until_seq=` parameter rather than a filter the page
applies, so what you are looking at is a prefix of the record itself.

If the stored record has been edited or a row removed, the replay says so, names
the first record that no longer verifies, and tells you it is not evidence.

### The post-incident review

`POST /incidents/{id}/pir`. Generated from the record, not from anybody's memory: sixteen sections from the
incident summary through to follow-up actions. It **refuses to generate at all**
if the audit chain does not verify.

Lessons come in two states:

- **VERIFIED** - every citation resolves to a record this incident produced.
- **PROPOSED** - it does not, and the review shows you which citations failed.

A model may suggest a lesson. It can never promote one. That gate is the reason
the next incident can trust what this one wrote down.

### Drills

Game days are data. `drills/recovery-collision.yaml` is a whole incident on a
virtual clock: it runs in under a minute, needs no model and no network, and
asserts thirty-five checkpoints across a hundred and thirty-two steps against
the real platform.

```bash
python -m pytest signalforge/tests/drills
```

Chaos is locked to drill scope by the contract: the chaos adapter offers exactly
one credential scope, `drill`, and no published route exposes injection at all.

---

## Part 4 - Operating it

Ports, environment, data locations, Postgres and rollback are in
`docs/09-deployment.md`. Day to day:

- **`/health`** - liveness with per-source detail.
- **`/metrics`** - Prometheus. The series worth an alert is
  `signalforge_policy_floor_rejections_total`: non-zero means a control actively
  prevented an unsafe configuration.
- **Dead letters** - Settings → Bus. Failed deliveries are visible and requeueable.
- **Integration health** - Settings → Integrations. A connector that stops
  answering is reported; it does not take the cockpit with it.
- **Traceability** - `make evidence REQ=REQ-062` resolves a requirement to its
  implementation, its tests, and live events from the running platform.

### If the model gateway is down

Nothing breaks. Narratives fall back to deterministic text, badges say
`degraded`, and `system.llm.degraded` appears on the timeline. Detection,
hypothesis ranking, policy, the gateway, validation and replay never used the
model in the first place.

---

## The twenty-three screens

Captured from the running product rather than drawn, and regenerated whenever the
screens change (`playbook/design/capture-product.cjs`). If one of these does not
match what you see, the picture is wrong and the product is right.

| Screen | What it is | Picture |
|---|---|---|
| S1 | Landing and sign-up | `screens/S01-landing.png` |
| S2 | Organisation setup: invites, roles, on-call, escalation | `screens/S02-org-setup.png` |
| S3 | Onboarding wizard | `screens/S03-wizard-connect.png`, `screens/S03-wizard-map.png` |
| S4 | Applications and their readiness | `screens/S04-applications.png` |
| S5 | An application: golden signals, journeys, changes | `screens/S05-application-detail.png` |
| S9 | The war-room cockpit | `screens/S09-war-room.png` |
| S10 | The incident brain, expanded | `screens/S10-incident-brain.png` |
| S11 | Next best action, its plan and its approval | `screens/S11-next-best-action.png` |
| S12 | The execution ledger | `screens/S12-execution-ledger.png` |
| S13 | Blast radius and topology | `screens/S13-blast-radius.png` |
| S14 | Warning and guidance | `screens/S14-warning-and-guidance.png` |
| S15 | Communications | `screens/S15-communications.png` |
| S16 | The layered recovery verdict | `screens/S16-recovery-verdict.png` |
| S17 | The same room, per role | `screens/S17-*.png` (six) |
| S18 | The IC copilot | `screens/S18-copilot.png` |
| S19 | Drill console | *no screen, by design - see below* |
| S20 | Closed: audit replay and the review | `screens/S20-replay-and-pir.png` |
| S21 | Platform settings | `screens/S21-settings.png` |
| S22 | Mobile paging, at 390 wide | `screens/S22-mobile-paging.png` |
| S23 | Integration health | `screens/S23-integration-health.png` |

**S19 has no screen and that is deliberate.** The drill engine is structurally
unreachable from the API: no published route names a drill, chaos or injection,
and the API package does not import the engine at all - both asserted by tests.
The console is `scripts/demo.sh` and `scripts/drill_run.py`. A drill route would
trade a guarantee for a page.

S6 (the watch banner) and S7 (the proposal sheet) are states of S5 rather than
screens of their own, and S8 (the room forming) is the state S9 is in before
anybody has joined.

## Known limitations

Honest list, carried from `docs/22-release-package.md`:

1. **The golden tiles carry no sparkline.** The detection route serves a reading
   per region with its baseline and threshold, not a history, so the only line
   the tile could draw would be one point per region - which renders as a trend
   and is not one. The tile shows the reading against its threshold instead.
   `/apps/{id}/rollups` is where a history would come from.
2. **The demonstration world heals itself.** Every chaos scenario carries a
   maximum duration the *application* enforces, so a walkthrough that runs longer
   than ten minutes finds a healthy application. Re-arm with
   `python scripts/drill_run.py drills/demo-setup.yaml --refault`; it does not
   touch the incident record.
3. **Single node, SQLite.** Postgres-ready, not Postgres-tested.
4. **`next` and `postcss` carry high advisories** whose remediation is a major
   Next upgrade, deferred deliberately. See `docs/19`.
5. **`pip-audit` could not run** on the build machine (TLS interception). No
   claim is made about the Python dependency set.
6. **No Drill Author agent.** Drills are written as data by hand.
7. **Performance figures are single-process and in-process.** They are not a
   production-scale claim, and `docs/17` says so.
8. **The twelve demonstration checks are derived** from the compliance map in
   `playbook/00-INDEX.md`, because the guide's own section 16 text is not in this
   repository. Confirm the list against the guide before a formal demonstration.

## Getting help from the record

Almost every question has an answer in the platform rather than in a document:

| Question | Where |
|---|---|
| Why did it think that? | the hypothesis card's `explain` |
| Who approved this? | the execution ledger, and the replay entry |
| What rule stopped me? | the policy decision's `rules_hit` |
| Did that action actually run? | the ledger state, and `reconcile` if it is UNCERTAIN |
| What did we tell customers? | the communications board, with contradictions |
| What did we learn? | the PIR, with VERIFIED separated from PROPOSED |
| Is this record trustworthy? | the replay's chain status |
