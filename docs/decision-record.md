# Decision record

Owner: engineering. The authored memory graph (signalforge-memory-graph.md) was not
present in the workspace at S0-T04; its import is pending (sprint 0 log, "Not built").
Meanwhile this file records decisions actually taken, newest first. Format:
id / date / decision / why / consequences.

## DR-008 (2026-09-01) Stories and RTM are generated views
docs/03 and docs/20 are rendered by scripts from specs/requirements.yaml plus recorded
test results. Why: one source of truth; handwritten copies drift. Consequence: editing
those docs by hand is prohibited; regenerate instead.

## DR-007 (2026-09-01) 51 requirements instead of ~40
Granularity chosen so each sprint's tests tag precisely (e.g. UNCERTAIN handling
REQ-062 separate from gateway decisions REQ-061). Ids are stable and never renumbered.

## DR-006 (2026-09-01) Every prohibited control is floor
specs/controls.yaml marks all prohibited entries floor: true and a test enforces it.
Why: a "configurable prohibition" is an oxymoron; the floor is the demo's security story.

## DR-005 (2026-09-01) Reconcile-before-retry lives in the actions SPI
ActionsAdapter.get_execution(idempotency_key) is part of the frozen contract, not an
infrastructure afterthought. Why: "API succeeded but response lost" must be answerable
by every actions integration or safe retries are impossible (guide section 06).

## DR-004 (2026-09-01) Chaos inject requires an explicit scope argument
No default value, enforced by test. Why: chaos outside an authorized drill scope is
prohibited at the floor; the SPI shape should make the unsafe call unwritable.

## DR-003 (2026-09-01) Push ingest signs timestamp.body with HMAC-SHA256
Headers X-SignalForge-Signature/-Timestamp(/-Key-Id), 300 s replay window, event_id as
the idempotency unit, 202 async. Why: replay resistance plus at-least-once semantics
without synchronous processing on the ingest path.

## DR-002 (2026-09-01) Envelope carries dual time and store-assigned fields
occurred_at (may follow a drill virtual clock) vs recorded_at (wall, store-assigned);
seq, payload_hash, prev_hash also store-assigned. Why: drills replay realistically
(S2/S7) while the audit chain stays strictly in recording order.

## DR-001 (2026-09-01) check_clean assembles its own pattern from halves
scripts/check_clean.sh greps for the reference-app name without containing it, so the
guard cannot trip itself and needs no self-exclusion. Consequence: the sprint log and
docs must reference "the reference app" generically outside allowed paths.
