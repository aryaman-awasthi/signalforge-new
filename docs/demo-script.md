# Final demonstration script

Owner: engineering. Sprint 7. Status: complete.

Two halves, and both are runnable:

| Half | Command | Takes | Proves |
|---|---|---|---|
| Machine-checkable | `python scripts/demo_check.py` | ~4 min | All twelve checks, from the tests that back them |
| Performed | this document, in a browser | ~20 min | The same twelve, in front of people |

---

## Before you start

```bash
bash reset.sh          # deterministic baseline: deletes what a run wrote, nothing else
bash run.sh            # waits until each component answers, then prints where it is
bash scripts/demo.sh   # in a second terminal: onboards, drives traffic, injects the fault
```

`run.sh` refuses quietly-broken states: nothing is reported up until it has
answered a real request, and if something essential does not come up the script
exits non-zero with the log path.

**No `GENAILAB_API_KEY` is required.** With the key absent every AI surface
answers deterministically and shows a `degraded` badge. That is a supported mode
and it is worth demonstrating on purpose - see check 5.

---

## Where the twelve checks come from

The engineering guide's section 16 is the authority and **its text is not in this
repository**. The twelve below are derived from the guide's own compliance map,
recorded in `playbook/00-INDEX.md`, which maps guide sections 03, 05, 06, 07, 08,
09+10, 11, 12, 13, 14, 16 and 17 to where each is satisfied - together with the
two checks this repository names explicitly: the live traceability challenge
(`make evidence REQ-xxx`) and a control shown actively blocking an unsafe action,
which `docs/08-agent-architecture.md` records as the guide's twelfth.

**This derivation is the one thing in Sprint 7 a reviewer should confirm against
the guide before the demonstration.** If the guide's list differs, the mapping in
`scripts/demo_check.py` is the single place to change: each check is a title, a
requirement set and a list of tests.

---

## The narrative

The checks are not twelve unrelated demonstrations. They are one incident, told
in order:

> onboard → detect → declare → run the room → reason → recommend → **a control
> blocks the unsafe thing** → a human approves → the action's outcome is unknown
> → reconcile → validate independently and refuse to close → govern the
> communication → replay and review → prove the trace is live

---

## Check 1 - The whole product boots from a clean machine, with no Docker

*Guide section 17: the product is demoable end to end.*

**Do:** `bash reset.sh && bash run.sh`

**Show:** the readiness lines, then `http://127.0.0.1:8000/health` and the
cockpit at `http://127.0.0.1:3000`.

**Expect:** each component reported up only after answering; `/health` `ok`;
the banner naming every URL and whether a model gateway is configured.

**Requirements:** REQ-000, REQ-073 · **Tests:** `signalforge/tests/test_repo_layout.py`

**If it goes wrong:** a busy port is overridden with `SIGNALFORGE_PORT` /
`SIGNALFORGE_WEB_PORT`. If the reference harness will not build (maven absent),
`run.sh` says so and carries on - only check 3's *live* half depends on it, and
its deterministic half does not.

---

## Check 2 - Any application onboards through public contracts only

*Guide section 05: complete product layers.*

**Do:** walk the onboarding wizard for the reference harness - connect a
connector, run a connection test (including the deliberate failure state),
auto-discover, map golden signals and journeys, upload topology, set SLOs, read
the readiness score.

**Show:** `POST /apps`, `POST /connectors`, `PUT /apps/{id}/mapping` in
`/docs`; then `bash scripts/check_clean.sh`.

**Expect:** a fully onboarded application with no seeds, and the app-agnostic
guard green - the core contains no reference to the application by name.

**Requirements:** REQ-030, REQ-036, REQ-038 · **Tests:** `signalforge/tests/onboarding`,
`signalforge/tests/reference/test_reference_onboarding.py`

**If it goes wrong:** the wizard has no private endpoints. Post the same bodies
over the API; steps 2-14 of `drills/recovery-collision.yaml` are literally those
calls.

---

## Check 3 - Detection corroborates, proposes a severity, and declares nothing

*Guide section 03: human, AI and software collaboration.*

**Do:** let `scripts/demo.sh` drive steady traffic, then inject the published
fault. Watch the stance move Steady → Watch → Proposed.

**Show:** `GET /apps/{id}/detection` and the app cockpit.

**Expect:** a **P0 proposal** corroborated by two independent signal families
with one customer-facing, carrying `requires_human: true` and
`declared: false`. Point at the second field: **the platform has proposed and
declared nothing.** An incident exists only when a person presses Declare.

**Requirements:** REQ-040, REQ-041, REQ-042, REQ-046 ·
**Tests:** `signalforge/tests/detection/test_detection_flow.py`,
`signalforge/tests/drills/test_recovery_collision.py`

**If it goes wrong:** the deterministic drill reaches the same proposal in
seconds - `pytest signalforge/tests/drills/test_recovery_collision.py` - because
it drives the same detector on a virtual clock.

---

## Check 4 - The war room, paging, escalation, and a late joiner who catches up

*Guide section 12: state and resumability.*

**Do:** declare as the Incident Commander. Page an SME. Let the countdown run
out. Then sign in as a *different* responder and read the join brief.

**Show:** the ack board with per-row countdowns, `/my/pages`,
`GET /incidents/{id}/brief`.

**Expect:** the unanswered page escalates to the next person in the rotation
without anybody pressing anything; a different user with the right role picks the
incident up and the brief tells them what they missed.

**Requirements:** REQ-043, REQ-044, REQ-045 · **Tests:** `signalforge/tests/warroom`

**If it goes wrong:** escalation can be forced rather than waited out -
`POST /incidents/{id}/pages/{page_id}/escalate` returns the successor page.

---

## Check 5 - Incident Brain: three hypotheses, moving on evidence, every number explained

*Guide section 08: AI controls and accountability.*

**Do:** refresh the brain. Post a measurement in the war room
("connection-pool is at 100% utilisation"). Refresh. Then post a contradicting
statement ("connection-pool is fine and healthy"). Refresh again.

**Show:** `GET /incidents/{id}/brain` (hypothesis cards with `explain` and
`discriminating_test`), `/brain/guidance` (the contradiction),
`/brain/topology` (CONFIRMED vs PREDICTED impact as two lists).

**Expect:** confidence moves, and the card shows the arithmetic that moved it
plus the next test that would move it further. The contradiction between a human
claim and telemetry is **surfaced, not averaged away**. Blast radius never mixes
a measurement with a prediction.

**Requirements:** REQ-050, REQ-052, REQ-053, REQ-054 ·
**Tests:** `signalforge/tests/brain/test_sprint5_acceptance.py`

**Worth doing on purpose:** run this with no model key. Everything above still
happens and the badge says `degraded`. The AI is an accelerator here, never a
dependency.

---

## Check 6 - A control actively blocks an unsafe action

*Guide section 07, and the guide's twelfth check.*

**Do:** two refusals, back to back.

1. Ask the gateway to run the high-risk rollback automatically:
   `POST /incidents/{id}/executions` with `intent: "auto"`.
2. As the **org owner**, holding every permission in the organisation, try to
   configure that rollback to auto-execute: `POST /apps/{id}/policy` with
   `auto_execute: true`.

**Show:** the blocked ledger row; the `422` naming `FLOOR-HIGH-RISK-NO-AUTO`;
the locked row in the policy editor; `policy.floor.rejected` in the audit chain;
`signalforge_policy_floor_rejections_total` on `/metrics`.

**Expect:** the execution is **BLOCKED** naming `FLOOR-AUTO-EXECUTE-REFUSED`
and **nothing reaches the application**. The policy edit is refused outright and
the refusal is written to the chain, because the 422 is seen only by the person
who tried.

**Requirements:** REQ-060, REQ-016 ·
**Tests:** `signalforge/tests/governance/test_policy_floor.py`,
`test_sprint6_acceptance.py::test_a_control_actively_prevents_an_unsafe_configuration`

**The point to say out loud:** this is not a permission the owner lacks. It is a
floor in code that no configuration can lower.

---

## Check 7 - A human approves, and it is a different human

*Guide section 03: human authority over consequential action.*

**Do:** request the rollback properly. Try to approve it as the person who
requested it. Then approve it as a second commander.

**Show:** `approval_required` with the floor rule named; the refusal of
self-approval; the granted approval recording which permission was exercised and
how long it lives.

**Expect:** separation of duty is enforced by the domain, not by the UI - the
entity refuses an approval by the requester whatever permissions they hold.

**Requirements:** REQ-061 · **Tests:** `signalforge/tests/governance/test_tool_gateway.py`,
`signalforge/tests/drills/test_drill_engine.py::test_virtual_time_expires_a_real_approval`

**If it goes wrong:** if the approval expired mid-demo, *that is the
demonstration*. Show the refusal, then request and approve again.

---

## Check 8 - "The API succeeded but the response was lost"

*Guide sections 06 and 11: architecture defence and exception handling.*

**Do:** run the approved rollback against a target that stops answering. Try to
retry. Then reconcile. Then submit the same action again.

**Show:** the execution ledger's states in order.

**Expect:**
- **UNCERTAIN**, with `error_category: timeout` and `retry_blocked: true`;
- the retry refused `409` telling you to reconcile first;
- reconciliation asking the application under the **derived** idempotency key and
  returning **COMPLETED_LATE**;
- the duplicate submission recognised, returning the original execution and
  sending nothing.

**Requirements:** REQ-062 · **Tests:** `signalforge/tests/governance/test_tool_gateway.py`,
`signalforge/tests/nfr/test_resilience.py::test_an_adapter_timeout_lands_uncertain_and_blocks_the_retry`

**The point to say out loud:** a timeout is not a failure. The platform does not
know whether production changed, it says so, and it refuses to roll back twice
to find out.

---

## Check 9 - Independent validation refuses to close on infrastructure health

*Guide section 11: exception handling.*

**Do:** recover the estate but leave the customer journey failing. Run
validation. Try to close.

**Show:** the four layers - technical, service, customer, business - each with
its checks and their sources; the closure lock; the `409` on close.

**Expect:** technical passes, service passes, **customer fails**, verdict
`not_recovered`, closure blocked naming the customer layer. The failure becomes
an evidence node on the graph with a `result_ref` that can be opened.

**Requirements:** REQ-065 · **Tests:** `signalforge/tests/governance/test_layered_validation.py`

**The point to say out loud:** validation reads the application, never the
incident brain. A platform that graded its own reasoning would always pass.

---

## Check 10 - Communications are governed: nothing auto-sends, corrections need approval

*Guide section 08: AI controls and accountability.*

**Do:** draft a customer statement from the graph. Approve it as the Comms Lead.
Issue it. Let the evidence move. Re-read the board. Draft a correction and have
it approved by somebody other than its author.

**Show:** the cited node ids on the draft; the contradicted flag with the
published text **unchanged**; the correction's redline.

**Expect:** nothing is ever sent by the platform. A contradiction is a warning,
never an edit. A correction is a new communication with its own approval, and an
externally visible one cannot be approved by its author.

**Requirements:** REQ-064 · **Tests:** `signalforge/tests/governance/test_communications.py`

---

## Check 11 - Audit replay and PIR - and tampering is detected

*Guide section 14: observability and audit evidence.*

**Do:** sign in as the **Auditor** - a role holding `read_audit` and nothing
else. Replay the incident. Generate the PIR. Then (in the test, never on a live
log) edit one stored audit payload and replay again.

**Show:** `GET /incidents/{id}/replay` - each line with its actor by name, the
policy rules that justified it, what changed, and what can be opened;
`POST /incidents/{id}/pir` - sixteen sections and lessons split into VERIFIED and
PROPOSED.

**Expect:** the chain reports valid; the PIR generates deterministically with no
model. After the edit, the replay reports **invalid**, names the first broken
record and says the replay is not evidence - and the PIR **refuses to generate at
all**.

**Requirements:** REQ-072, REQ-016 · **Tests:** `signalforge/tests/audit/test_audit_replay.py`,
`signalforge/tests/audit/test_pir.py`

**The point to say out loud:** a lesson becomes part of institutional memory only
when every citation resolves to a record this incident produced. A model may
propose a lesson; it can never promote one.

---

## Check 12 - Live traceability

*Guide sections 09 and 10: traceability and SPECS/DSL.*

**Do:** ask the audience to pick a requirement id from
`specs/requirements.yaml`. Then:

```bash
make evidence REQ=REQ-062      # or whichever they picked
make rtm && make report
```

**Show:** the requirement text, the source files that implement it, the
REQ-tagged tests and their last outcome, the topics that requirement publishes,
and **events pulled live from the running platform** for those topics.

**Expect:** nothing is hand-mapped. The runtime evidence is derived from the
topics the implementation actually defines, so a requirement whose code stopped
publishing would stop showing evidence.

**Requirements:** REQ-006 · **Tests:** `signalforge/tests/test_traceability.py`

**If it goes wrong:** with no incident live, `make evidence` still resolves the
requirement, its implementation and its tests - only the runtime events are
empty, and the tool says which of the two it is.

---

## Verifying all twelve without a browser

```bash
python scripts/demo_check.py            # runs every check's backing tests
python scripts/demo_check.py --list     # the full mapping, including fallbacks
python scripts/demo_check.py --only 6   # one check
```

The mapping in that script - title, guide section, action, surface, expected
result, requirements, tests, fallback - is the same mapping this document
performs. If the two ever disagree, the script is the one that has been executed.
