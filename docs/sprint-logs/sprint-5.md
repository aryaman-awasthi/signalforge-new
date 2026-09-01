# Sprint 5 log (2026-09-01, in progress)
Goal: the reasoning core with explainable, versioned intelligence and LLM discipline.

## Built

- [S5-T02][REQ-051] **The llm.provider adapter, complete.** Delivered ahead of the rest
  of Sprint 5 as the outcome of a repository-wide audit of AI traffic, and it is the
  whole of S5-T02: **S5-T03 onwards must reuse it rather than rebuild it.**

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
  mid-run and the system continues" a property of every future call site rather than a
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
  an SDK, or if `verify=False` is ever hardcoded. The normal suite never touches the
  network - the gateway is exercised through a real `httpx.AsyncClient` over a
  `MockTransport`, so request construction, headers, JSON bodies and status handling are
  all production code paths - and the test containers resolve their model settings from
  an empty environment, so a developer with a key exported cannot change what the suite
  proves.

## Not built / deferred

- S5-T01, S5-T03, S5-T04, S5-T05, S5-T06 remain. This entry covers S5-T02 only.
- Model-path capacity is proven against a mock transport; a live-gateway latency and
  cost profile lands with the agents that generate real load (S5-T03).

## Deviations

- S5-T02 was taken out of sprint order. Human instruction: *"Audit the entire
  repository's LLM/embedding usage and ensure ALL runtime AI traffic goes through the
  TCS GenAI Lab LiteLLM gateway... This work overlaps S5-T02. If you fully implement the
  llm.provider here, make that explicit in the commit/log so Sprint 5 reuses it rather
  than rebuilding it."*
- No vendor SDK was added. The gateway speaks the OpenAI-compatible protocol and it is
  two endpoints, so the client is httpx - already a dependency - rather than a library
  per vendor. The mixed fleet behind the gateway (`azure/...`, `azure_ai/...`,
  `gemini-...`) is then a configured string.
- `specs/requirements.yaml`: REQ-051 status `planned` -> `built`. A status flip, not a
  contract change; the acceptance text is untouched.

## Tests

- added: 66 (33 provider behaviour, 27 provider-boundary/security, 6 bus-and-wiring
  integration) + 3 opt-in live smoke tests, skipped unless a key is deliberately
  enabled.
- suite result: see docs/21-test-report.md.

## Artifacts updated

- `docs/06-solution-architecture.md` (§2 stack row, new §2.1 model-path configuration)
- `docs/10-data-design.md` (regenerated: `llm_cache`)
- `docs/12-threat-model.md` (AI-3 exfiltration row: mitigation built and tested)
- `docs/20-RTM.md`, `docs/21-test-report.md` (regenerated)
- `.env.example` (new: the config template, no values)

## Security notes

- New egress surface: outbound HTTPS to `genailab.tcs.in` only, enforced by an in-code
  allowlist rather than by configuration (threat-model row AI-3).
- New stored data: `llm_cache` holds model answers and provenance, never prompts, keyed
  and read per tenant (REQ-013).
- `/metrics` gained three model-path series; every label is a closed vocabulary
  (classification, outcome, degraded reason), so an upstream gateway cannot grow the
  cardinality of an unauthenticated scrape.
- No security floor was weakened: TLS verification cannot be turned off, the credential
  never reaches a log or an event, and the audit chain and policy floor are untouched.

## Known issues and technical debt

- The token ledger is in-process, which is right for the single-node deployment and sits
  behind `domain/llm/ports.TokenLedger` for a shared counter later.
- The pre-call budget check uses a ~4-characters-per-token estimate, so a single call
  can overshoot a budget by its own size; unbounded overspend cannot happen.
- `GET /v1/models` validation is opt-in (`scripts/llm_smoke.py`) rather than a startup
  probe, deliberately: a boot that blocks on a network round trip is the wrong trade for
  an incident command centre.

## Demoable checkpoint

```bash
python scripts/llm_smoke.py --dry-run   # config resolves to the approved gateway
python scripts/llm_smoke.py             # with GENAILAB_API_KEY set: live chat + embeddings
python -m pytest signalforge/tests/llm -q
```

With no key set, boot the platform (`bash run.sh`) and everything works: the model path
reports itself degraded and every deterministic path is unchanged.

## Coverage checklist ticks

- LLM provider discipline: configured endpoint and models, structured outputs, retries,
  cache, budgets, degraded fallback emitting `system.llm.degraded` (REQ-051).

## Entry criteria for next sprint: n/a - Sprint 5 is in progress.
