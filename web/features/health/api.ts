import { api } from "@/lib/api/client";

export interface HealthResponse {
  status: string;
  service: string;
  version: string;
  time: string;
}

export function fetchHealth(): Promise<HealthResponse> {
  return api.get<HealthResponse>("/health");
}
