/**
 * Adapters, connector bindings and ingest keys (REQ-030, REQ-032, REQ-036).
 *
 * Feeds three surfaces: the wizard's Connect step, the S23 integration health
 * screen and the adapter/ingest sections of S21 settings. All of it goes
 * through the cookie proxy; browser code never holds a bearer token.
 *
 * Two rules are visible in the types. An `IngestKey` has no `token` and no
 * `secret` field, because the listing endpoint has none - the credential exists
 * exactly once, in the 201 body of an issue or a rotate, which is why
 * `IssuedIngestKey` is a separate type and never enters a list. And a
 * `Connector` carries the *names* of its credential fields, never their values.
 */

import { proxyApi } from "@/lib/api/client";

export type ConnectorStatus = "up" | "degraded" | "down" | "unknown";
export type AdapterMode = "pull" | "push" | "invoke";
export type CredentialScope = "read_only" | "remediation" | "drill";

export type ConfigSchema = {
  type?: string;
  required?: string[];
  additionalProperties?: boolean;
  properties?: Record<
    string,
    {
      type?: string;
      description?: string;
      minLength?: number;
      enum?: string[];
      default?: unknown;
    }
  >;
};

export type Adapter = {
  id: string;
  name: string;
  version: string;
  kind: string;
  mode: AdapterMode;
  direction: string;
  vendor: string;
  description: string;
  availability: string;
  status: string;
  runnable: boolean;
  simulated: boolean;
  capabilities: string[];
  unsupported: string[];
  mutating_capabilities: string[];
  credential_scopes: CredentialScope[];
  auth: { method: string; credential_fields: string[]; header: string | null };
  limits: {
    timeout_s: number;
    max_calls_per_minute: number;
    max_result_bytes: number;
    max_rows: number;
  };
  redaction: { policy_id: string | null; fields: string[]; patterns: string[] };
  config_schema: ConfigSchema;
};

export type Registry = {
  adapters: Adapter[];
  /** Manifests that would not load. Shown, never swallowed: a silently short
   * registry is how an installation ends up wondering where its integration went. */
  errors: { path: string; error: string }[];
};

export type CapabilityMatrix = {
  rows: {
    adapter_id: string;
    name: string;
    kind: string;
    mode: string;
    availability: string;
    simulated: boolean;
    credential_scopes: string[];
    cells: { capability: string; supported: boolean; mutating: boolean }[];
  }[];
  coverage: {
    kind: string;
    capabilities: string[];
    adapters: string[];
    covered: string[];
    uncovered: string[];
    complete: boolean;
  }[];
};

export type Connector = {
  id: string;
  app_id: string;
  adapter_id: string;
  adapter_name: string | null;
  name: string;
  kind: string | null;
  mode: AdapterMode | null;
  direction: string | null;
  credential_scope: CredentialScope;
  config: Record<string, unknown>;
  credential_fields: string[];
  enabled: boolean;
  simulated: boolean;
  installed: boolean;
  status: ConnectorStatus;
  status_detail: string | null;
  last_checked_at: string | null;
  last_ok_at: string | null;
  last_error: string | null;
  consecutive_failures: number;
  created_at: string | null;
  updated_at: string | null;
};

/** One row of S23. `freshness_s` is the pull side; everything `ingest_*` plus
 * `last_event_at` is the push side, joined from the live ingest keys. */
export type ConnectorHealth = Connector & {
  freshness_s: number | null;
  last_event_at: string | null;
  event_freshness_s: number | null;
  ingest_keys: number;
  ingest_paths: string[];
  ingest_accepted: number;
  ingest_duplicates: number;
  ingest_rejected: number;
  ingest_last_error: string | null;
};

export type IngestStream =
  | "changes"
  | "alerts"
  | "topology"
  | "messages"
  | "tickets";

export type IngestKey = {
  id: string;
  app_id: string;
  stream: IngestStream;
  name: string;
  path: string;
  binding_id: string | null;
  rotated_from: string | null;
  /** The first characters of the token, so two keys can be told apart. The
   * token itself was returned once and the platform cannot show it again. */
  token_prefix: string;
  active: boolean;
  created_at: string | null;
  created_by: string;
  revoked_at: string | null;
  revoked_by: string | null;
  last_used_at: string | null;
  last_event_at: string | null;
  freshness_s: number | null;
  last_error: string | null;
  accepted_count: number;
  duplicate_count: number;
  rejected_count: number;
};

/** The 201 body of an issue or a rotate, and the only shape in the cockpit that
 * ever carries credential material. It is held in component state, shown once,
 * and never written to storage of any kind. */
export type IssuedIngestKey = IngestKey & { token: string; secret: string };

// --- calls -------------------------------------------------------------------

export const listAdapters = () => proxyApi.get<Registry>("/adapters");

export const capabilityMatrix = () =>
  proxyApi.get<CapabilityMatrix>("/adapters/capability-matrix");

export const listConnectors = (appId?: string) =>
  proxyApi.get<Connector[]>(
    `/connectors${appId ? `?app_id=${encodeURIComponent(appId)}` : ""}`,
  );

export const connectorHealth = (appId?: string) =>
  proxyApi.get<ConnectorHealth[]>(
    `/connectors/health${appId ? `?app_id=${encodeURIComponent(appId)}` : ""}`,
  );

export type CreateConnectorBody = {
  app_id: string;
  adapter_id: string;
  name: string;
  credential_scope: CredentialScope;
  config: Record<string, unknown>;
  credentials: Record<string, string>;
  enabled?: boolean;
};

export const createConnector = (body: CreateConnectorBody) =>
  proxyApi.post<Connector>("/connectors", body);

export const patchConnector = (id: string, body: Record<string, unknown>) =>
  proxyApi.patch<Connector>(`/connectors/${encodeURIComponent(id)}`, body);

export const deleteConnector = (id: string) =>
  proxyApi.del<void>(`/connectors/${encodeURIComponent(id)}`);

export const listIngestKeys = (appId?: string) =>
  proxyApi.get<IngestKey[]>(
    `/ingest-keys${appId ? `?app_id=${encodeURIComponent(appId)}` : ""}`,
  );

export const issueIngestKey = (body: {
  app_id: string;
  stream: IngestStream;
  name: string;
  binding_id?: string | null;
}) => proxyApi.post<IssuedIngestKey>("/ingest-keys", body);

export const rotateIngestKey = (id: string) =>
  proxyApi.post<IssuedIngestKey>(`/ingest-keys/${encodeURIComponent(id)}/rotate`);

export const revokeIngestKey = (id: string) =>
  proxyApi.del<IngestKey>(`/ingest-keys/${encodeURIComponent(id)}`);
