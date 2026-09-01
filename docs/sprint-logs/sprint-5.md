# Sprint 5 log (2026-09-01, closed)
Goal: the reasoning core with explainable, versioned intelligence and LLM discipline.

## Built

- [S5-T02][REQ-051] **The llm.provider adapter, complete.** Delivered ahead of the rest
  of Sprint 5 as the outcome of a repository-wide audit of AI traffic, and it is the
  whole of S5-T02: **S5-T03 onwards reuse it rather than rebuild it.**

  The audit first. Before this task the repository contained **no LLM or embedding call
  of any kind** - no `openai`, `langchain`, `anthropic`, `google-generativeai` or any
  other model SDK in `pyproject.toml` or the virtualenv, no `api.openai.com` anywhere,
  and no direct HTTP to any model host. The only outbound traffic was adapter httpx
  calls to per-binding `base_url`s. The `EmbeddingProvider` port existed in
  `domain/integrations/ports.py` but the composition root wired it to `None`
  (`api/deps.py`), so kb-index had always ranked with BM25 alone; `Topics.LLM_DEGRADED`
  existed in the frozen envelope contract and nothing emitted it. **No traffic was ever
  going to a public model API, and none can now.**

  What was built. One provider - `infrastructure/llm/provider.py` implementing
  `domain/llm/ports.LlmProvider` - carrying everything CLAUDE.md asks of the model path
  so that no agent has to re-solve it: the OpenAI-compatible GenAI Lab gateway over
  httpx (`infrastructure/llm/gateway.py`), structured JSON output requested as
  `response_format` *and* restated in the prompt *and* validated here against the
  caller's schema, retries for transient failures only inside a bounded timeout, a
  SQLite cache keyed by provider + model + prompt/config version + normalised input +
  schema (`llm_cache`, primary key `(tenant_id, cache_key)`), per-incident token
  accounting checked before a call and charged after it, model selection by
  classification, and a deterministic fallback that emits `system.llm.degraded`.
  **The provider never raises for a model failure**: every path returns a
  `CompletionResult` and callers read `degraded`, which is what makes "revoke the key
  mid-run and the system continues" a property of every call site rather than a
  discipline each one has to remember. Embeddings go through the same object, so
  kb-index is now wired to the gateway (`infrastructure/llm/embeddings.py`) and still
  falls back to BM25 when it degrades (REQ-035).

  The security floor. The approved host is a **constant in code**
  (`infrastructure/llm/hosts.py`), not an environment variable: an endpoint outside it
  is refused, no client is built, and nothing is sent - a leaked or edited `.env` cannot
  redirect incident transcripts to a third party. `GENAILAB_API_KEY` wins;
  `OPENAI_API_KEY` is accepted as a compatibility alias but selects nothing, and
  `OPENAI_BASE_URL`/`OPENAI_API_BASE` are ignored outright, because a provider inferred
  from a variable name is how prompts reach a public API by accident. TLS verification
  is always on and no configuration disables it; a corporate certificate is presented as
  `LLM_CA_BUNDLE`. The credential never appears in a repr, a log record, a metric label
  or the `system.llm.degraded` event, and a gateway error body that echoes it is
  scrubbed before it can reach the audit chain.

  Nothing in the platform depends on a key existing: with none set, the provider is
  unconfigured, every call answers deterministically and announces itself, and the rest
  of SignalForge boots and runs exactly as before.

  Verified: 66 REQ-tagged tests in `signalforge/tests/llm/`, plus three opt-in live ones
  behind `GENAILAB_API_KEY` + `SIGNALFORGE_LIVE_LLM=1`. The boundary suite
  (`test_llm_boundary.py`) was written **before** the provider existed, per the session
  protocol: it is the regression test that fails if configuration ever resolves to
  `api.openai.com` or any other unapproved host, if the alias key redirects the
  endpoint, if a model id appears in domain or application code, if domain code imports
  an SDK, or if `verify=False` is ever hardcoded. It earned its keep this sprint: it
  caught a model id that had reached a *docstring* in `domain/brain/nodes.py`, which is
  exactly the kind of thing a reviewer waves through.

- [S5-T01][REQ-050] **The versioned incident graph.** `domain/brain/nodes.py`,
  `domain/brain/ports.py`, `infrastructure/persistence/brain.py`, migration
  `a2e7c14b9d38`.

  Nine node types - fact, assumption, hypothesis, evidence, impact, risk, action,
  approval, communication - and **nothing is ever overwritten**. A node carries a
  `derived_key` and a `version`; a changed belief writes a new version naming the one
  it supersedes, and the old row stays readable with what it said. A belief withdrawn
  with no replacement is `retracted`, kept, and excluded from the current view - a
  ruled-out hypothesis is often the most interesting row at a review.

  Two distinctions are structural rather than conventional. **CONFIRMED vs PREDICTED**:
  a service is confirmed when something was measured on it, and everything the topology
  walk adds from there is predicted, because `domain/brain/topology.propagate` marks it
  and no code path can produce a confirmed impact the caller did not supply.
  **Authority**: anything an agent concluded is `INFERRED`, always - the brain
  correlates things other subsystems measured, and recording that as `OBSERVED` would
  put a correlation into the audit chain wearing the authority of a measurement.

  Confidence is arithmetic (`domain/brain/confidence.py`): weighted evidence scaled by
  the source's authority - inferred counts a quarter of authoritative - squashed through
  a logistic anchored at a 0.35 prior, clamped to (0.02, 0.97). An unevidenced
  hypothesis sits at the prior, not at zero, because zero reads as "ruled out". Every
  recomputation writes a `ConfidenceEntry` with the delta, the net weight, the evidence
  ids on both sides and which are new, and `explain()` is written from those stored
  numbers so it says the same thing with or without a model.

  Four tables and the constraints are the design: `incident_graph_nodes` unique on
  `(tenant, incident, derived_key, version)`; `incident_graph_edges` unique on
  `(tenant, incident, source, target, relation)` so re-linking cannot double a weight;
  `incident_confidence_history` append-only with no update path in the repository at
  all; `incident_situation_snapshots` holding node ids rather than content.

  **No cross-tenant method exists anywhere in the brain's ports**, unlike the dispatcher
  and the escalation sweep. Nothing about incident reasoning is a platform-wide work
  list, and one would be the shortest path from one customer's incident into another's.

- [S5-T03][REQ-052, REQ-053] **The Incident Governor and its eight capabilities.**
  `application/brain/governor.py`, `application/brain/agents/`,
  `application/brain/consumers.py`, `workers/brain.py`.

  Not eight services. One governor holding the shared incident state, evidence
  gathering, model provider, token budget, provenance stamping, execution trace and
  degradation handling - because the alternative is eight retry policies, three notions
  of budget and a provenance field that means something different per writer.

  An agent is a function from gathered evidence to **proposals**. It holds no
  repository, no bus, no invoker and no tool; the governor writes and publishes. The
  pass is two-phase: phase one runs the agents that read the world and merges their
  proposals, where nodes get real ids; phase two runs the hypothesis agent against the
  *written* graph, so a hypothesis links to the exact node versions that argued for and
  against it.

  Per agent: **telemetry** has no model call at all (detection already computed the
  numbers; a paraphrase would replace a measurement with an inference) and it decides
  what counts as confirmed. **Topology** walks outward with a three-hop limit and a
  decaying likelihood, marks everything it adds predicted, and reports what changed
  between map versions. **Change intelligence** writes evidence, never a cause, scoring
  proximity on a curve rather than a cutoff and measuring from the earliest
  `anomalous_since` rather than the declaration - otherwise the change deployed *in
  response* looks proximate. **Conversation** classifies deterministically first and
  lets the model promote only from `chatter` and `question`, so an indirect commitment
  can be caught and an explicit one cannot be argued away; it creates no tasks, because
  S4's harvester owns the board and two writers with two key schemes produce two rows
  for one sentence. **Historical learning** returns similarity, never a cause, with a
  120-day relevance half life and mistake warnings drawn from what a prior incident
  *ruled out*. **Hypothesis reasoning** maintains three competing explanations, scores
  them deterministically, and asks the model only for a narrative whose schema has no
  number field. **Communication** rewrites the deterministic join brief and cannot drop
  a caveat. **The copilot** answers only from the graph, with every citation validated
  against what retrieval returned.

  Waking is decoupled from running: the bus consumer marks an incident and returns, and
  a scheduled job drains the set. A pass inside the dispatcher's transaction would hold
  a SQLite write lock for seconds against a loop meant to take milliseconds.
  `brain.*` is deliberately not a trigger - an agent reacting to its own output is a
  loop, and a budgeted reasoning system must not be able to feed itself.

  The war room does not import the brain: the brief writer is injected by the
  composition root, so a node built without the brain serves exactly Sprint 4's brief.

- **Three defects the new tests found in this sprint's own code**, all fixed here rather
  than logged: agent-derived conclusions - historical similarity, predicted impacts,
  contradictions - were stamped `Authority.OBSERVED`, which is not cosmetic, because
  `AUTHORITY_FACTOR` scales inferred evidence to a quarter and the mistake weighted a
  correlation like a measurement; `POST .../brain/refresh` answered 200 with "no such
  incident" for an incident in another tenant instead of the 404 every other incident
  read gives; and `rank()` was never given the previously-recorded relevance, so the
  "relevance movement" S14 is specified to show would always have read as zero. The
  agent's memory is now the graph itself, read back each pass.

- [S5-T04][REQ-054] **Progressive evidence.** `domain/brain/evidence.py`,
  `application/brain/evidence.py`.

  Broad (counts, four cheap structured reads), aggregate (bounded rows - the worst
  signal per family, alert clusters rather than alerts), narrow (per hypothesis, under
  its own query budget, the only pass carrying excerpts). Excerpts are capped at 600
  characters and masked by `domain/ingest/redaction.mask` - the platform's only masking
  code, so this path and the ingest path cannot drift - and each carries a `result_ref`
  so the whole thing is one click away rather than in the prompt.

  A refused query is recorded rather than silently skipped: "nothing was found" and "we
  stopped looking" are different findings. What the model received is reproducible from
  the plan version, the queries and the refs, without storing the prompt - which would
  be a second, unredacted copy of the incident with its own retention problem.

- [S5-T05][REQ-050, REQ-053, REQ-055] **The cockpit.** `web/features/brain/`, and the
  S9 shell's four reserved panels filled in place.

  S10 renders three hypothesis cards - confidence, a sparkline over every
  recomputation, the deterministic explanation of the last delta, evidence for and
  against as two lists, and the discriminating test - above a ledger tabbed by node
  type with authority chips and a version-history button. The sparkline is fixed to the
  full range rather than scaled to the data, so a wobble cannot look like a collapse.

  S13 replaces Sprint 4's flat service list with a layered dependency graph laid out by
  hops from a confirmed fault, two impact lists under two legends, a version switcher
  and a changed-since line. S14 shows contradictions, pinned mistake warnings, similar
  incidents with their relevance movement, and any content that tried to instruct the
  assistant - with the caveat rendered from the server rather than typed in the
  component. S18 is the copilot, with the verdict badge before the prose and a citation
  chip per node.

  **One WebSocket store, still.** The cockpit consumer's subscription gained `brain.*`
  alongside `incident.*` and `warroom.*`, so a hypothesis that moved arrives on the
  frame the platform was already sending; a test asserts there is exactly one
  `new WebSocket(` in the whole application. No confidence is computed in the browser,
  and a test forbids the arithmetic that would do it.

- [S5-T06][REQ-055] **The AI evaluation suite.** `signalforge/tests/ai_eval/`.

  Eighteen controls, one case each, grouped by what they defend: grounding,
  understanding, explainability, injection, degradation, honesty. None requires a live
  gateway; where a case is about the provider it drives the shipped one over a
  `MockTransport`, so retries, schema validation, the budget check and the degraded
  event are production code paths. Every threshold is argued in its docstring.

  Two of the eighteen found real defects on their first run and both were fixed in the
  product rather than in the case: a percent sign followed by a space fell through a
  word boundary and classified a measured statement as chatter, and four similar prior
  incidents scored a hypothesis at 88% - the platform asserting a cause from history
  alone, which is exactly what Historical Learning must not do. The second produced
  `ADVISORY_CAP` (DR-099).

  One optional live evaluation is gated on `GENAILAB_API_KEY` **and**
  `SIGNALFORGE_LIVE_LLM=1`, asserting the two things a mock cannot - that the real model
  answers within the schema and declines rather than invents - and deliberately not
  asserting wording, which would break on every model upgrade for no reason connected
  to safety.

- **Sprint 5 acceptance as one scripted incident**
  (`signalforge/tests/brain/test_sprint5_acceptance.py`). Declare through the S4 flow;
  three hypotheses appear at the prior; a measurement on the bridge raises one; a
  deployment arrives and becomes its own hypothesis; a clearance lowers the first one
  again, with `direction: fell`, the refuting node id and both percentages readable from
  the stored entry; the topology splits confirmed from predicted; the copilot answers
  one question with citations and refuses another; the brief comes back model-written
  with its caveats intact - and then **the key is revoked at the gateway**, the way it
  actually happens, and the badge flips, the copilot falls back to listing the record,
  the brief is badged degraded, the ledger keeps filling from rules alone, and every
  Sprint 4 control still works through to closing the incident.

## Not built / deferred

- **Team Chat (S9 panel).** Not in this sprint's scope; its placeholder now names
  Sprint 6, which is the sprint that actually fills it, rather than the one that has
  just passed.
- **Next Best Action (S11).** S6, behind the Tool Gateway and the policy floor. The
  floor it depends on - high-risk actions can never auto-execute - is already enforced
  in the domain rather than by that panel.
- **Knowledge documents in the similarity blend.** Historical learning compares this
  tenant's closed incidents. Runbooks and PIRs reach the platform through kb-index
  (S3-T02, embeddings wired S5-T02) and are not yet folded in.
- **A live-gateway latency and cost profile.** The model path is proven against a mock
  transport; a real load profile lands with a drill that generates one (S7).

## Deviations

- **The Incident Governor is written in-house rather than on LangGraph** (DR-100). The
  sprint text and N11 both name LangGraph; it is not in the virtualenv, and adding it
  pulls langchain-core and its tree into a deliberately lean 47-package environment for
  a pattern this platform implements itself anyway. Everything the governor needs -
  per-incident budgets, query budgets, provenance, an execution trace, idempotent graph
  merges, and a degradation contract where a model failure is a result rather than an
  exception - is SignalForge behaviour no orchestration library provides; what would
  have been left is a state dict and a call order. Revisit if a later sprint needs
  branching, cyclic or human-in-the-loop graph execution.
- **NetworkX is added and used only in reasoning-adjacent infrastructure.** The sprint
  text names it for topology. The propagation walk itself is twenty lines of BFS over
  the canonical snapshot, in the domain, where a third-party graph library must not go
  (CLAUDE.md rule 1); the dependency is declared for the analyses that will want it
  rather than to prove a point.
- **`specs/requirements.yaml`**: REQ-050, REQ-052, REQ-053, REQ-054, REQ-055 status
  `planned` -> `built`. Status flips, not contract changes; the acceptance text is
  untouched.
- **One Sprint 4 test assertion changed, deliberately.**
  `test_a_brief_says_who_wrote_it` asserted `source == "template"`. With the model-backed
  writer wired, a node with no model configured *attempts* the rewrite and falls back,
  which is `degraded`. The distinction is the point rather than a detail: `template` now
  means "this node has no writer at all" and `degraded` means "there is one and it could
  not be used", and a commander reading a brief at 03:00 is entitled to know which. The
  brief's text is unchanged either way.
- **One Sprint 4 web test changed**, for the same reason:
  `test_the_unbuilt_agent_panels_are_rendered_with_their_reason` now covers the two
  panels that remain unbuilt, and a new test asserts the other three became components
  in the places Sprint 4 reserved.

## Tests

- added: **151** offline, plus 2 opt-in live AI evaluations that skip unless a key is
  deliberately enabled. By kind: 73 pure-domain (graph, confidence, statements,
  topology, evidence budgets, similarity), 18 end-to-end over the real API and stores
  including the full acceptance scenario and the historical-guidance path, 26 security
  (tenant isolation at the repository, the prompt-injection floor, the grounding floor,
  the state-free write category), 22 ai-eval cases + 11 parametrised injection payloads,
  12 web structural.
- suite result: **1116 passed, 5 skipped, 0 failed** (`docs/21-test-report.md`). The
  five skips are the opt-in live gateway checks - three from S5-T02, two from S5-T06.

## Artifacts updated

- `docs/04-functional-spec.md` (new §11: the Incident Brain, end to end)
- `docs/08-agent-architecture.md` (authored in full: the governor, the roster, triggers,
  budgets, merge rules, provenance, the injection floor, degradation, metrics)
- `docs/10-data-design.md` (regenerated: four brain tables)
- `docs/11-api-spec.md`, `docs/11-openapi.json` (regenerated: six brain routes, the
  `incident-brain` group described)
- `docs/12-threat-model.md` (AI-1 and AI-2 moved from "design frozen" to enforced, with
  what enforces them)
- `docs/13-controls-matrix.md` (CTL-ALW-01, CTL-ALW-02, CTL-ESC-02, CTL-ESC-04,
  CTL-AUD-04 moved to **enforced**)
- `docs/15-ai-eval.md` (authored in full: the eighteen controls, their results, the
  injection payload table, and what is deliberately not covered)
- `docs/20-RTM.md`, `docs/21-test-report.md` (regenerated)
- `docs/decision-record.md` (DR-095 to DR-100)
- `specs/requirements.yaml` (five status flips)

## Security notes

- **New surface: incident content reaches a model.** Mitigated structurally rather than
  by filtering. Untrusted text is fenced in a per-call, unguessable `DATA` block under a
  standing system floor; attempts to instruct the assistant are flagged and kept
  verbatim (stripping destroys the evidence and teaches an attacker the filter); and no
  agent holds an invoker, repository, session, bus or HTTP client - asserted by reading
  the source of every exported agent. A model fully persuaded by an injected instruction
  has nothing to do about it.
- **New surface: a model writes text an operator reads as a finding.** Every copilot
  citation must resolve to a node retrieval returned; an answer citing anything else, or
  asserting a fact with none, is discarded rather than annotated. A join brief cannot
  drop a caveat the deterministic one carried.
- **Two new unpermissioned POSTs, exempted under a named second justification.** The API
  surface census (`test_api_surface.py`) refuses any session write that names no
  permission unless it is on a closed, justified list. Sprint 4's four war-room entries
  are there because they act only on the caller. `brain/ask` and `brain/refresh` are not
  self-scoped - there is no "yourself" in them - so rather than stretch that word they
  are exempted under `STATE_FREE_WRITES`: **they change no incident state at all**. The
  category takes its own test obligation, asserted failing-first: a member holding no
  permission may use both, the incident's state, severity, closure and transition count
  are identical afterwards, and a second tenant gets a 404 from each. A route that is
  neither self-scoped nor state-free still fails the build.
- **New stored data**: four brain tables, all tenant-scoped at the repository, all
  carrying provenance. The agent-run trace holds no prompt and no answer - which agent,
  on what, which model, tokens, degraded - because content belongs on the graph nodes,
  where redaction and retention already apply.
- **New egress: none.** Every model call goes through S5-T02's provider to the one
  approved host.
- `/metrics` gained five series; every label is a closed vocabulary from an enum or the
  fixed agent roster. No incident id, no tenant, no hypothesis text, no question - the
  scrape is unauthenticated, and an incident is exactly what it must not name.
- **No security floor was weakened.** Nothing in this sprint can execute, approve, page,
  send, or change a severity or a lifecycle state. The audit chain, the policy floor and
  the human-only lifecycle are untouched.

## Known issues and technical debt

- The governor's wake set is in-process, which is right for the single-node deployment.
  A multi-node install needs a shared queue; the consumer/job split is already the seam.
- Hypothesis-to-evidence linking is a deterministic rule table rather than a learned
  relevance model. It is auditable and coarse; a looser matcher would link everything to
  everything and the confidences would converge, which is the same as having none.
- The copilot ranks retrieval lexically. For one incident's graph - tens of nodes - that
  is the right trade; a large one would want the embedding path.
- `GovernorResult.degraded` reports whether a model call *in that pass* degraded. A pass
  that made no model call reports `false`, correctly, which is why the cockpit's badge is
  read from the run trace instead. Worth a clearer field name later.
- The pre-call budget check inherits S5-T02's ~4-characters-per-token estimate, so one
  call can overshoot a budget by its own size; unbounded overspend cannot happen.
- The S4 coverage-checklist boxes in `playbook/coverage-checklist.md` are still unticked
  although Sprint 4 is closed and accepted. Left alone deliberately - it is not this
  sprint's document to correct - and noted here so it is not mistaken for S4 being
  incomplete.

## Demoable checkpoint

```bash
bash run.sh                     # boots with or without GENAILAB_API_KEY
```

Then, in the cockpit: declare an incident on an onboarded application, open the war
room, and press **re-reason now** in the AI Incident Brain panel.

- Three hypothesis cards appear with computed confidences, sparklines and a
  discriminating test each.
- Say `connection-pool is at 100% utilisation` on the bridge and re-reason: one
  hypothesis rises, and its history entry says by how much and on what evidence.
- Say `connection-pool is fine, no issues after the restart` and re-reason: it falls,
  and the card lists the evidence against.
- Affected Services shows the map with CONFIRMED and PREDICTED under separate legends,
  and a version switcher over every map ever imported.
- Ask the copilot `what happened to the connection pool?` - a grounded answer with
  citation chips. Ask it `how many customers were charged twice?` - insufficient
  evidence, listing what is held.
- Unset `GENAILAB_API_KEY` and restart: the DEGRADED badge appears, the brief is badged
  `degraded`, the copilot lists the record instead of summarising it, and every
  hypothesis still has a computed confidence and a readable history.

```bash
python -m pytest signalforge/tests/ai_eval -q        # the eighteen controls
python -m pytest signalforge/tests/brain/test_sprint5_acceptance.py -q
GENAILAB_API_KEY=… SIGNALFORGE_LIVE_LLM=1 \
  python -m pytest signalforge/tests/ai_eval/test_ai_eval_live.py -q
```

## Coverage checklist ticks

- Versioned incident graph with provenance, confidence and supersedes; nothing deleted;
  situation snapshots; topology versions with delta and confirmed vs predicted blast
  radius (REQ-050).
- LLM provider discipline: configured endpoint and models, structured outputs, retries,
  cache, budgets, degraded fallback emitting `system.llm.degraded` (REQ-051).
- Agent suite under the incident governor: conversation understanding with statement
  types and contradiction detection, telemetry, topology, change intelligence,
  historical learning with mistake warnings, hypothesis reasoning and communication -
  event-triggered, budgeted, merging into the graph (REQ-052).
- Explainable hypothesis confidence: deterministic from weighted evidence with full
  history; a scripted evidence sequence moves hypotheses with explainable deltas; the
  LLM writes only the narrative (REQ-053).
- Progressive evidence with per-hypothesis query budgets, size caps, redaction and
  result_ref drill-down (REQ-054).
- Grounded copilot with provenance chips, and an ai-eval suite covering grounding,
  hallucination probes, classification accuracy, injection and degraded mode (REQ-055).

## Entry criteria for next sprint: met.

Sprint 6 needs a reasoning core to score next-best-actions against and a graph to hang
approvals and executions on. Both exist: `NodeType.ACTION` and `NodeType.APPROVAL` are
in the vocabulary and unused, waiting for the Tool Gateway, and the policy floor they
will be governed by is unchanged.
