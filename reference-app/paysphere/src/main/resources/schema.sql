-- PaySphere ledger schema (H2, file mode).
--
-- `executions` is the load-bearing table: every action writes its row *before*
-- the side effect is attempted, so an action whose caller timed out is still
-- discoverable by idempotency key afterwards. That row is what
-- GET /observe/actions/executions/{idempotency_key} returns and what makes
-- reconcile-before-retry possible on the platform side.

create table if not exists payments (
  payment_id      varchar(64) primary key,
  region          varchar(24)  not null,
  journey         varchar(24)  not null,
  amount_minor    bigint       not null,
  currency        varchar(8)   not null,
  state           varchar(16)  not null,
  synthetic       boolean      not null,
  trace_id        varchar(64),
  route           varchar(128),
  latency_ms      double,
  failure_reason  varchar(256),
  created_at      timestamp with time zone not null,
  updated_at      timestamp with time zone not null
);
create index if not exists idx_payments_region_state on payments(region, state);
create index if not exists idx_payments_created on payments(created_at);

create table if not exists settlements (
  settlement_id   varchar(64) primary key,
  payment_id      varchar(64)  not null,
  region          varchar(24)  not null,
  state           varchar(16)  not null,
  created_at      timestamp with time zone not null,
  settled_at      timestamp with time zone
);
create index if not exists idx_settlements_region_state on settlements(region, state);

create table if not exists executions (
  idempotency_key varchar(256) primary key,
  execution_id    varchar(64)  not null,
  action_id       varchar(64)  not null,
  state           varchar(16)  not null,
  params          varchar(4096),
  output          varchar(4096),
  error           varchar(1024),
  requested_at    timestamp with time zone not null,
  started_at      timestamp with time zone,
  finished_at     timestamp with time zone
);
create unique index if not exists idx_executions_execution_id on executions(execution_id);
create index if not exists idx_executions_action on executions(action_id, requested_at);

create table if not exists chaos_injections (
  injection_id    varchar(64) primary key,
  idempotency_key varchar(256) not null,
  scenario_id     varchar(64)  not null,
  target          varchar(128) not null,
  params          varchar(4096),
  state           varchar(16)  not null,
  scope           varchar(128) not null,
  expires_at      timestamp with time zone,
  started_at      timestamp with time zone,
  ended_at        timestamp with time zone
);
create unique index if not exists idx_chaos_idempotency on chaos_injections(idempotency_key);
create index if not exists idx_chaos_state on chaos_injections(state);

create table if not exists config_versions (
  region          varchar(24)  not null,
  version         int          not null,
  settings        varchar(4096) not null,
  source          varchar(64)  not null,
  applied_at      timestamp with time zone not null,
  primary key (region, version)
);
