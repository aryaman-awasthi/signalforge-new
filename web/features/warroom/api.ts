/**
 * The war room (REQ-045), through the cookie proxy like every other feature.
 *
 * Three shapes here exist because the API publishes a distinction the cockpit
 * must not flatten:
 *
 * - `required`, `paged` and `present` on a member are three booleans, not one
 *   status. "Four were required, four were paged, two are here" is the sentence
 *   an incident commander needs at minute ten.
 * - `countdown_s` beside `ack_deadline_at` on a page. The deadline is the fact;
 *   the countdown is the same fact against the *server's* clock at the moment of
 *   the read. The board ticks from the server's number rather than from
 *   `Date.now()` against a parsed timestamp, so a laptop with a skewed clock
 *   still shows a timer that agrees with the escalation sweep.
 * - `source` on a brief - `template` today, `model` from S5. A reader who cannot
 *   tell generated prose from assembled facts cannot calibrate how much to trust
 *   it, so the badge is rendered from this rather than assumed.
 */

import { proxyApi } from "@/lib/api/client";

import type { Severity } from "@/features/incidents/api";

export type PageState =
  | "pending"
  | "sent"
  | "acked"
  | "escalated"
  | "failed"
  | "cancelled";

export type BlastRadius = {
  summary: string;
  regions: string[];
  regions_total: number;
  journeys: string[];
  services: string[];
  customer_facing: boolean;
  journey_tier: number | null;
  truncated: boolean;
  /** `proposal` when detection had a verdict at declare time, `declaration`
   * when only the severity a person typed was available. */
  source: string;
};

export type MembershipGap = {
  role: string;
  reason: string;
  detail: string;
};

export type RoleRequirement = {
  role: string;
  reason: string;
  /** False means present rather than woken. An executive is in the room for a
   * P0; they are not paged for one. */
  paged: boolean;
};

export type Member = {
  user_id: string;
  display_name: string;
  role: string | null;
  reason: string;
  source: string;
  required: boolean;
  paged: boolean;
  present: boolean;
  joined_at: string | null;
};

export type PageDelivery = {
  notification_id: string;
  adapter_id: string;
  channel: string;
  status: string;
  simulated: boolean;
  detail: string;
};

export type PageRow = {
  id: string;
  incident_id: string;
  recipient: string;
  recipient_display: string;
  role: string | null;
  reason: string;
  state: PageState;
  severity: Severity;
  hop: number;
  ack_timeout_s: number;
  created_at: string;
  paged_at: string | null;
  ack_deadline_at: string | null;
  /** Seconds left, from the server. Negative once overdue, null once settled. */
  countdown_s: number | null;
  overdue: boolean;
  acked_at: string | null;
  acked_by: string | null;
  ack_note: string;
  escalated_to_page_id: string | null;
  escalated_from_page_id: string | null;
  /** False means no channel took it. That page failed at once rather than
   * counting down toward an escalation to channels that also do not exist. */
  reached: boolean;
  detail: string;
  deliveries: PageDelivery[];
};

export type Task = {
  id: string;
  incident_id: string;
  title: string;
  status: "open" | "done" | "cancelled";
  source: "bridge" | "manual";
  created_at: string;
  created_by: string;
  owner_user_id: string | null;
  /** The name a bridge actually said, when the platform could not resolve it to
   * exactly one user. The task is visibly unassigned rather than owned by a
   * guess. */
  owner_label: string;
  assigned: boolean;
  due_at: string | null;
  due_label: string;
  overdue: boolean;
  note: string;
  /** What was said on the bridge, verbatim. How a disputed task is settled. */
  evidence_ref: string;
  completed_at: string | null;
  completed_by: string | null;
};

export type WarRoom = {
  id: string;
  incident_id: string;
  reference: string;
  app_id: string;
  severity: Severity;
  state: string;
  title: string;
  opened_at: string;
  opened_by: string;
  closed_at: string | null;
  bridge_channel: string | null;
  bridge_adapter_id: string | null;
  has_bridge: boolean;
  blast_radius: BlastRadius;
  required_roles: RoleRequirement[];
  /** Required roles nobody holds. Rendered, never hidden: a P0 with an empty
   * comms-lead seat is something to find out at minute two. */
  gaps: MembershipGap[];
  members: Member[];
  present_count: number;
  pages: PageRow[];
  tasks: Task[];
};

export type Brief = {
  incident_id: string;
  generated_at: string;
  /** `template` today, `model` from S5, `degraded` when a model fell back. */
  source: "template" | "model" | "degraded";
  headline: string;
  what_is_happening: string[];
  impact: string[];
  what_we_know: string[];
  what_has_been_done: string[];
  change_context: string[];
  what_we_need: string[];
  /** Always populated. A brief drawn from a declaration made while the detector
   * was down says so rather than reading like a corroborated one. */
  caveats: string[];
  text: string;
};

export type BridgeMessage = {
  message_id: string;
  author: string;
  author_display: string;
  text: string;
  at: string;
  simulated: boolean;
  thread_id: string | null;
};

export type Bridge = {
  incident_id: string;
  channel: string | null;
  has_bridge: boolean;
  detail: string;
  messages: BridgeMessage[];
};

export type PageList = { count: number; pages: PageRow[] };
export type TaskList = { count: number; tasks: Task[] };

// --- calls -------------------------------------------------------------------

const inc = (id: string) => encodeURIComponent(id);

export const getWarRoom = (incidentId: string) =>
  proxyApi.get<WarRoom>(`/incidents/${inc(incidentId)}/war-room`);

export const joinWarRoom = (incidentId: string, note?: string) =>
  proxyApi.post<WarRoom>(`/incidents/${inc(incidentId)}/war-room/join`, {
    note: note ?? "",
  });

export const getBrief = (incidentId: string) =>
  proxyApi.get<Brief>(`/incidents/${inc(incidentId)}/brief`);

export const listPages = (incidentId: string) =>
  proxyApi.get<PageList>(`/incidents/${inc(incidentId)}/pages`);

/** Self-scoped by construction: there is no recipient parameter, which is what
 * lets the mobile paging page carry no permission. */
export const myPages = (openOnly = true) =>
  proxyApi.get<PageList>(`/my/pages?open_only=${openOnly ? "true" : "false"}`);

export const acknowledgePage = (
  incidentId: string,
  pageId: string,
  note?: string,
) =>
  proxyApi.post<PageRow>(
    `/incidents/${inc(incidentId)}/pages/${inc(pageId)}/ack`,
    { note: note ?? "" },
  );

/** Returns the *successor* page - the one now ringing. */
export const escalatePage = (incidentId: string, pageId: string) =>
  proxyApi.post<PageRow>(
    `/incidents/${inc(incidentId)}/pages/${inc(pageId)}/escalate`,
    {},
  );

export const raisePage = (
  incidentId: string,
  body: { user_id: string; reason?: string; role?: string; ack_timeout_s?: number },
) => proxyApi.post<PageRow>(`/incidents/${inc(incidentId)}/pages`, body);

export const getBridge = (incidentId: string) =>
  proxyApi.get<Bridge>(`/incidents/${inc(incidentId)}/bridge`);

export const postToBridge = (incidentId: string, text: string) =>
  proxyApi.post<Bridge>(`/incidents/${inc(incidentId)}/bridge`, { text });

export const listTasks = (incidentId: string) =>
  proxyApi.get<TaskList>(`/incidents/${inc(incidentId)}/tasks`);

export const createTask = (
  incidentId: string,
  body: { title: string; owner_user_id?: string; owner_label?: string; due_at?: string },
) => proxyApi.post<Task>(`/incidents/${inc(incidentId)}/tasks`, body);

export const updateTask = (
  incidentId: string,
  taskId: string,
  body: { owner_user_id?: string; due_at?: string; cancel?: boolean; note?: string },
) =>
  proxyApi.patch<Task>(
    `/incidents/${inc(incidentId)}/tasks/${inc(taskId)}`,
    body,
  );

export const completeTask = (incidentId: string, taskId: string, note?: string) =>
  proxyApi.post<Task>(
    `/incidents/${inc(incidentId)}/tasks/${inc(taskId)}/done`,
    { note: note ?? "" },
  );
