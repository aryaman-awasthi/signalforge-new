/**
 * The single WebSocket store (CLAUDE.md: one WebSocket store for the whole app).
 * Sprint 0 stub: connection lifecycle + topic subscription shape are fixed here;
 * the real /ws wiring lands with the event backbone UI (Sprint 2).
 */

export type WsStatus = "disconnected" | "connecting" | "connected";

export interface WsEnvelope {
  topic: string;
  seq: number;
  payload: unknown;
}

type Listener = (event: WsEnvelope) => void;

export const WS_URL =
  process.env.NEXT_PUBLIC_WS_URL ?? "ws://127.0.0.1:8000/ws";

class WsStore {
  private listeners = new Map<string, Set<Listener>>();
  status: WsStatus = "disconnected";

  connect(): void {
    // Stub: real connection, backoff and resume-from-seq arrive in Sprint 2.
    this.status = "disconnected";
  }

  subscribe(topic: string, listener: Listener): () => void {
    const set = this.listeners.get(topic) ?? new Set<Listener>();
    set.add(listener);
    this.listeners.set(topic, set);
    return () => {
      set.delete(listener);
    };
  }
}

export const wsStore = new WsStore();
