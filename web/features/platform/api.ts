/** Platform settings data access, through the cookie proxy like everything else. */

import { proxyApi } from "@/lib/api/client";

export type ComponentHealth = {
  component: string;
  status: "up" | "degraded" | "down" | "unknown";
  detail: string | null;
};

export type WorkerJob = {
  name: string;
  interval_s: number;
  runs: number;
  failures: number;
  last_run_at: string | null;
  last_error: string | null;
  last_result: string | null;
  healthy: boolean;
};

export type PlatformStatus = {
  status: ComponentHealth["status"];
  version: string;
  checked_at: string;
  clock: { source: "wall" | "virtual"; now: string; speed: number; paused: boolean };
  bus: {
    head_seq: number;
    max_lag: number;
    groups: Record<string, number>;
    dead_letters: Record<string, number>;
  };
  worker: { running: boolean; jobs: WorkerJob[] };
  components: ComponentHealth[];
  retention: {
    plane: string;
    pruned_through_seq: number;
    pruned_count: number;
    pruned_at: string;
    retention_days: number;
  }[];
};

export type DeadLetter = {
  id: string;
  group: string;
  event_seq: number;
  event_id: string;
  topic: string;
  partition_key: string;
  lane: number;
  attempts: number;
  error: string;
  state: "dead" | "requeued" | "resolved";
  first_failed_at: string;
  last_failed_at: string;
  requeued_at: string | null;
  requeued_by: string | null;
  resolved_at: string | null;
};

export const platformStatus = () => proxyApi.get<PlatformStatus>("/platform/status");

export const listDeadLetters = (state?: DeadLetter["state"]) =>
  proxyApi.get<DeadLetter[]>(
    `/platform/dead-letters${state ? `?state=${state}` : ""}`,
  );

/**
 * drained_here says whether a consumer for that group runs on the node that
 * took the request. False is not a failure - the event is queued - but the
 * operator needs to know a worker has to be running somewhere for it to move.
 */
export type RequeueResult = DeadLetter & { drained_here: boolean };

export const requeueDeadLetter = (id: string) =>
  proxyApi.post<RequeueResult>(`/platform/dead-letters/${id}/requeue`);
