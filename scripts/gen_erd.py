"""Generate docs/10-data-design.md from the SQLAlchemy models.

The ERD is generated, never handwritten, for the same reason the RTM is: a
hand-drawn schema diagram is wrong the first time someone adds a column. This
reads Base.metadata - the same metadata Alembic autogenerates migrations from -
so the document and the database cannot drift.

The prose sections that a diagram cannot express (retention planes, the hash
chain, what tenant isolation relies on, the Postgres path) live in DESIGN_NOTES
below and are versioned with the code that makes them true.

Usage: python scripts/gen_erd.py [--out PATH]
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import MetaData

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GROUPS: list[tuple[str, str, tuple[str, ...]]] = [
    (
        "Identity and security (S1)",
        "Who the tenant is, who its people are, and the append-only record of "
        "what they did.",
        ("tenants", "users", "role_bindings", "invites", "sessions",
         "oncall_rotations", "escalation_policies", "secrets", "audit_log"),
    ),
    (
        "Event backbone (S2)",
        "The outbox and the log are one table: state and its event are written in "
        "the same transaction, and the same rows are what replay reads.",
        ("events", "consumer_offsets", "consumer_processed", "dead_letters",
         "event_seals", "retention_watermarks"),
    ),
    (
        "Adapter registry (S3)",
        "Manifests are files, not rows: what a deployment stores is which app is "
        "wired to which adapter, at which credential scope, and what the health "
        "loop last saw. Credentials are not here - they live encrypted in "
        "`secrets` under connector/<binding>/<field>.",
        ("connector_bindings",),
    ),
    (
        "Shipped integrations (S3)",
        "What the generic adapters remember. itsm-lite, teams-sim and page-sim "
        "simulate an integrated *system*; the record of what SignalForge did is "
        "durable either way, because a rehearsal whose pages and tickets vanish "
        "on restart proves nothing.",
        ("sim_tickets", "sim_messages", "notification_deliveries",
         "knowledge_documents"),
    ),
    (
        "Agent traces (S2, filled S5)",
        "Written from Sprint 5 by the agent suite; shipped now so agents are born "
        "observable rather than instrumented afterwards.",
        ("agent_runs", "agent_run_steps"),
    ),
]

DESIGN_NOTES = """
## 3. Retention planes

Two planes share one table and are told apart by the `plane` column.

| Plane | Contents | Lifetime | Sealed |
|---|---|---|---|
| `signal` | telemetry: metric samples, health polls, alerts, connector health | configured window, default 7 days, bounded 1-90 by the domain | no |
| `incident` | what an incident was: declarations, evidence, decisions, actions, communications | kept | yes |

Retention is the only code path that removes a recorded event
(`application/events/retention.py`). `RetentionPolicy.applies_to` has no branch
that can return true for the incident plane, and `SqlEventStore.prune` raises on
any plane but signal, so the two rules are enforced twice and neither is
configurable.

## 4. The hash chain and its anchors

Each event carries `payload_hash` (canonical sha256 of the payload, defined by
the frozen envelope contract) and `prev_hash` (the payload hash of the previous
event in the same chain). Chains are per **(tenant, plane)**: a single chain
across both planes would be broken by the first retention prune.

Pruning writes a row to `retention_watermarks` holding the payload hash of the
last event removed. Verification of the survivors starts from that anchor, which
is what distinguishes a prune (anchored, expected) from a deletion (unanchored,
detected). `event_seals` closes a range: the ordered `(seq, event_id,
payload_hash, prev_hash)` tuples are hashed together with the tenant's previous
seal, so seals chain onto each other and re-sealing a doctored range cannot
reproduce the original hash.

Because `prev_hash` points at a payload hash, two adjacent events with identical
payloads produce identical links; the seal is what binds position and identity,
and it is what an audit checks against.

## 5. Ordering and offsets

`events.lane` is assigned at write time from `blake2b(partition_key) % 16`
(`domain/events/lanes.py`). It is stored rather than computed on read because
the mapping must be identical across processes and restarts - Python's built-in
`hash()` is salted per process, and disagreement about a lane is silent
reordering. Each `(group, lane)` pair owns a row in `consumer_offsets`, so one
slow or dead-lettered partition never blocks another. Changing the lane count
re-shards the bus and needs a migration that recomputes every `lane` value.

`consumer_processed` is the idempotency ledger: `(group, event_id)`. The handler,
its ledger row and the offset advance commit in one transaction, so a crash
mid-delivery redelivers the event against unchanged state.

## 6. Tenant isolation

Every table that holds customer data carries `tenant_id`, and the predicate is
applied inside the repository rather than by handlers (REQ-013). Two paths are
deliberately cross-tenant and documented at their definitions:

- `EventStore.read_for_dispatch` - one dispatcher serves the whole install; it
  hands handlers a full envelope carrying `tenant_id`, and handler writes go
  through tenant-scoped repositories.
- `EventStore.tenants` - the retention sweep's work list.
- `ConnectorBindingRepository.list_enabled_for_health` - one connector health
  loop serves the install. Every binding it returns carries its own `tenant_id`,
  the health event is published on that tenant's timeline, and everything the
  loop writes back goes through the tenant-scoped methods.

`_platform` is a reserved tenant id used for events about the platform itself
(the database, the bus, the worker group). Because replay is tenant-scoped, no
customer can read them - and a customer's connector health deliberately does
*not* go there: it belongs to the tenant that owns the binding.

## 6a. Connector bindings and credentials

`connector_bindings` is keyed by `(tenant_id, app_id, adapter_id,
credential_scope)`. The scope is part of the key because read-only and
remediation are different credentials with different blast radii: they are two
rows, and revoking one leaves the other alone. The row holds
`credential_fields` - names only - while the values live in `secrets` as Fernet
ciphertext under `connector/<binding_id>/<field>`, so a binding can be listed,
logged and returned over the API without a decrypt, and deleting a binding
deletes exactly its own secrets.

The `status`, `status_detail`, `last_checked_at`, `last_ok_at`, `last_error` and
`consecutive_failures` columns are written by the connector health loop and are
what the integration health screen reads. They are durable on purpose: after a
restart the screen shows the last observed verdict and the last error rather
than an empty table until the next poll.

## 6b. What the shipped adapters store

Four tables belong to the generic adapters rather than to SignalForge's own
state, and they are on a separate unit of work (`IntegrationUnitOfWork`) for that
reason: an adapter call is not a use case and must not be able to commit a
transaction containing identity or incident state.

| Table | Owner | Why it is durable |
|---|---|---|
| `sim_tickets` | itsm-lite | a rehearsal of ticket sync needs tickets that survive a restart |
| `sim_messages` | teams-sim bridge and DMs | the script player schedules by writing rows at future `sent_at`, so the schedule survives a restart and follows the drill clock; reads window by time |
| `notification_deliveries` | every notification adapter | the receipt is written *before* the transport is touched, so "was this person told" survives a lost frame; acknowledgement is terminal |
| `knowledge_documents` | kb-index | `tokens` is stored so a search costs one read; `embedding` is present only when a provider was reachable, and its absence is what makes search fall back to BM25 rather than fail |

`notification_deliveries` is reachable from both units of work. The adapters
write receipts through `IntegrationUnitOfWork`; acknowledgement goes through the
identity one, because an acknowledgement, its audit entry and its event have to
be a single commit or an escalation timer and the audit chain can disagree about
whether a human answered.

## 7. Indexes

Every index exists for a query the platform actually makes:

| Index | Serves |
|---|---|
| `ix_events_tenant_seq` | replay paging (`tenant_id`, `seq > after_seq`) |
| `ix_events_chain` | chain verification and sealing per (tenant, plane) |
| `ix_events_lane_seq` | the dispatcher's per-lane sweep |
| `ix_events_incident` | incident-scoped timelines and seals |
| `ix_events_topic` | topic-filtered replay |
| `ix_events_causation` | causal-chain walks |
| `ix_dead_letters_tenant_state` | the operator's dead-letter pane |
| `ix_dead_letters_group_state` | the dispatcher draining requeues |
| `ix_audit_log_tenant_seq` | audit chain verification |
| `ix_connector_bindings_tenant_app` | the integration health screen, per app |
| `ix_connector_bindings_enabled` | the connector health loop's cross-tenant work list |
| `ix_sim_messages_channel_time` | the bridge's time-windowed read, which is also what hides a scripted line until it is due |
| `ix_notification_deliveries_recipient` | a reconnecting cockpit asking what is still open for this person |
| `ix_notification_deliveries_incident` | the war room's "who was told about this incident" |
| `ix_knowledge_documents_tenant_app` | the kb-index corpus read, tenant- and app-scoped |
| `ix_sim_tickets_tenant_app` | the simulated ticket queue per application |

## 8. Portability

SQLite in WAL mode today, Postgres by connection string: only portable column
types are used, `TZDateTime` re-attaches UTC on load because SQLite drops
tzinfo, and JSON columns hold small value objects rather than anything queried
relationally. Two behaviours are SQLite-specific and named here so a Postgres
move is deliberate rather than surprising:

1. `events.seq` is an autoincrement integer primary key. On Postgres this
   becomes a sequence; gaps from rolled-back transactions are already tolerated
   by every reader, which treats `seq` as ordering, not as a count.
2. The chain tip is read and written inside one transaction. SQLite serialises
   writers, so this is safe; Postgres needs an explicit lock or a unique
   constraint on `(tenant_id, plane, prev_hash)` to keep two concurrent
   appenders from claiming the same predecessor.
"""


def load_metadata() -> MetaData:
    from signalforge.infrastructure.persistence.models import Base

    return Base.metadata


def mermaid_erd(metadata: MetaData, tables: tuple[str, ...]) -> str:
    lines = ["```mermaid", "erDiagram"]
    present = [name for name in tables if name in metadata.tables]
    for name in present:
        table = metadata.tables[name]
        lines.append(f"    {name} {{")
        for column in table.columns:
            kind = _short_type(column)
            marks = []
            if column.primary_key:
                marks.append("PK")
            if column.foreign_keys:
                marks.append("FK")
            suffix = f' "{",".join(marks)}"' if marks else ""
            lines.append(f"        {kind} {column.name}{suffix}")
        lines.append("    }")
    for name in present:
        for column in metadata.tables[name].columns:
            for fk in column.foreign_keys:
                target = fk.column.table.name
                if target in present:
                    lines.append(f"    {target} ||--o{{ {name} : \"{column.name}\"")
    lines.append("```")
    return "\n".join(lines)


def _short_type(column) -> str:
    name = type(column.type).__name__.lower()
    return {"tzdatetime": "datetime", "largebinary": "bytes", "varchar": "string"}.get(
        name, name
    )


def table_section(metadata: MetaData, name: str) -> str:
    table = metadata.tables[name]
    rows = ["| Column | Type | Null | Notes |", "|---|---|---|---|"]
    for column in table.columns:
        notes = []
        if column.primary_key:
            notes.append("primary key")
        for fk in column.foreign_keys:
            notes.append(f"-> {fk.target_fullname}")
        if column.unique:
            notes.append("unique")
        rows.append(
            f"| `{column.name}` | {_short_type(column)} | "
            f"{'yes' if column.nullable else 'no'} | {', '.join(notes) or ''} |"
        )
    indexes = sorted(index.name for index in table.indexes)
    constraints = [
        f"unique ({', '.join(c.name for c in constraint.columns)})"
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    ]
    extras = []
    if indexes:
        extras.append("Indexes: " + ", ".join(f"`{i}`" for i in indexes))
    if constraints:
        extras.append("Constraints: " + ", ".join(constraints))
    body = "\n".join(rows)
    return f"#### `{name}`\n\n{body}\n" + ("\n" + "  \n".join(extras) + "\n" if extras else "")


def render(metadata: MetaData) -> str:
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    known = {name for _, _, tables in GROUPS for name in tables}
    ungrouped = sorted(set(metadata.tables) - known - {"alembic_version"})

    parts = [
        "# 10 - Data design",
        "",
        "Owner: engineering. Generated by `scripts/gen_erd.py` (`make erd`) from the",
        "SQLAlchemy models Alembic also autogenerates migrations from, so this document",
        "cannot drift from the schema. The design notes below are authored; the ERD and",
        "the column tables are not.",
        "",
        f"Generated: {generated}",
        f"Tables: {len(metadata.tables)}",
        "",
        "## 1. Entity relationship diagram",
        "",
    ]
    for title, blurb, tables in GROUPS:
        parts += [f"### {title}", "", blurb, "", mermaid_erd(metadata, tables), ""]
    if ungrouped:
        parts += [
            "### Ungrouped",
            "",
            "Tables not yet placed in a group - add them to GROUPS in scripts/gen_erd.py.",
            "",
            mermaid_erd(metadata, tuple(ungrouped)),
            "",
        ]

    parts += ["## 2. Tables", ""]
    for title, _blurb, tables in GROUPS:
        parts += [f"### {title}", ""]
        for name in tables:
            if name in metadata.tables:
                parts += [table_section(metadata, name), ""]
    for name in ungrouped:
        parts += [table_section(metadata, name), ""]

    parts.append(DESIGN_NOTES.strip())
    parts.append("")
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(ROOT / "docs" / "10-data-design.md"))
    args = parser.parse_args()

    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(load_metadata()), encoding="utf-8")
    print(f"gen_erd: wrote {output.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
