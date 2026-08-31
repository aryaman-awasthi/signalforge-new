# 02 - Future-state journey (v1, Sprint 0)

Owner: engineering. One narrative, steady state to institutional memory, naming the
screens (S-numbers) and controls involved. This is the journey the drills replay.

## Steady state
The payments app hums. Its team onboarded it months ago through the wizard (S3): pull
adapters read /observe/* endpoints, deploy hooks push to /api/v1/ingest/changes, topology
is versioned, SLOs and severity rules are set, baselines learned. The app detail screen
(S5) shows golden signals green in both regions. Integration health (S23) shows every
connector fresh; the simulated channels are labelled SIMULATED.

## Trouble
A deploy goes out at 14:02 - deployment.started arrives via the hook and a "Change window
open" chip appears (S5). At 14:07 checkout latency drifts in region-2. Rollups compare
against baseline; the breach sustains three evaluations; the fast SLO burn crosses ~14x.
Corroboration finds a second independent family (error rate, customer-facing journey
probe failing). SignalForge opens a Watch (S6) - a quiet banner, "AI is checking",
listing what it is verifying. No page has fired.

## Proposal, declaration
At 14:11 corroboration holds. A proposal sheet (S7) shows the evidence checklist, the
proposed severity from the impact matrix, and the change-window overlap. Only a human can
act on it: the on-call IC reads the evidence and clicks Declare P1. Lifecycle moves
Proposed -> Active; the itsm-lite ticket syncs; the audit chain records the human actor.

## War room
Declaration creates the war room (S9) and pages by blast radius: checkout SME, payments
Tech Lead, Comms Lead. The ack board (S8) shows per-row countdowns; one page is not
acknowledged and escalates to next-in-rotation on the visible timer. A late joiner gets
the join brief. The room's panels hold shared state: Incident Overview, Live Bridge
Transcript, Team Chat, AI Incident Brain, Next Best Action, AI Copilot, Incident
Timeline, Warning and Guidance, Health Score and Metrics, Affected Services.

## Reasoning
The Incident Brain (S10) holds three hypotheses with confidence from weighted evidence -
deterministic, versioned, nothing deleted. The change-intelligence agent links the 14:02
deploy diff to the onset. Conversation understanding classifies bridge statements; when an
engineer's "DB looks fine" contradicts a machine fact, the contradiction is surfaced, not
silently resolved. Historical learning pins a warning from a past incident: "restarting
checkout under storm made it worse (INC-2231)". The copilot (S18) answers only from graph
nodes, each claim with a provenance chip. When the LLM budget trips, the DEGRADED badge
appears and everything deterministic keeps working.

## Governed action
Next Best Action (S11) proposes rollback of the 14:02 deploy: score, alternatives,
fallback, expected validation time. Rollback is a high-risk class, so the Tool Gateway
requires approval - the floor makes auto-execution impossible, and the policy editor
(S21) shows those rows locked. The IC approves (identity, rule, expiry recorded). The
execution ledger (S12) steps through; one step times out -> UNCERTAIN, retry blocked;
reconciliation via the actions adapter returns COMPLETED_LATE; the ledger marks it,
prevents the duplicate, and continues. Comms (S15) drafts per audience, grounded on graph
nodes; when a grounded node is superseded the draft flips CONTRADICTED and a redlined
correction goes for approval. Nothing auto-sends.

## Recovery, closure
Layered validation (S16) reads the app independently of the graph: technical metrics,
journey probes, customer transactions, business counters. The stability ring fills; a
brief regression resets it. The customer layer stays red on stuck transactions and blocks
closure until cleared - then the IC closes. The incident plane is sealed with the hash
chain.

## Memory
The PIR generator drafts the timeline, verified causes, wrong assumptions and comms
corrections; only verified lessons pass the gate into the knowledge index. The audit
scrubber (S20) can re-render the graph at any sequence number for the auditor. A month
later, the same scenario runs as a drill (S19) on the virtual clock at 10x against the
reference harness - same screens, same controls, simulated channels labelled - and the
new SME acks their first page in a safe world.
