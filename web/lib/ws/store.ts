/**
 * The single WebSocket store (CLAUDE.md: one WebSocket store for the whole app).
 *
 * Every screen that wants live updates goes through this object and no other, so
 * a cockpit with six panels open holds one socket rather than six - which is
 * also what makes reconnect, back-off and the credential dance below something
 * written once.
 *
 * ## Why the connection begins with an HTTP call
 *
 * The session token lives in an httpOnly cookie. Browser JavaScript cannot read
 * it, deliberately - that is the S1 control that keeps a session out of reach of
 * an XSS - and a cross-origin WebSocket handshake sends no cookie and cannot set
 * an Authorization header. Handing the token to JavaScript would have bought a
 * refresh interval at the price of the control, so instead:
 *
 *   1. ask this app's own origin for a socket ticket (`POST /api/proxy/auth/ws-ticket`,
 *      which attaches the cookie server-side);
 *   2. spend it on the handshake as the `ticket, <value>` subprotocol - a header,
 *      never a query parameter, so it stays out of access logs;
 *   3. throw it away. It is single-use and lives thirty seconds, so every
 *      reconnect fetches a fresh one.
 *
 * A ticket that leaks is worth one handshake for half a minute and nothing at
 * all on the HTTP API, because the API's token verifier refuses it by type.
 *
 * ## What arrives, and what the screens do with it
 *
 * `notification` frames are the pager. `room.changed` frames are **nudges**:
 * they name an incident and the topic that moved and carry no incident data at
 * all, so a screen reacts by re-reading through the typed client - the same
 * authorised route it was already polling. The socket is therefore an
 * accelerator, never a source of truth and never a second read path.
 *
 * Which is why polling stays. A cockpit whose socket is down or whose ticket
 * endpoint is unreachable degrades to exactly the S4 behaviour and says so
 * through `status`; nothing on screen becomes wrong, only later.
 */

export type WsStatus = "disconnected" | "connecting" | "connected";

/** Everything the server sends. `type` is always present. */
export interface WsFrame {
  type: string;
  [key: string]: unknown;
}

/** A nudge: something moved in a room this socket is watching. */
export interface RoomChanged extends WsFrame {
  type: "room.changed";
  incident_id: string;
  topic: string;
  seq: number | null;
}

type Listener = (frame: WsFrame) => void;

export const WS_URL =
  process.env.NEXT_PUBLIC_WS_URL ?? "ws://127.0.0.1:8000/ws";

const TICKET_PATH = "/api/proxy/auth/ws-ticket";
const TICKET_SUBPROTOCOL = "ticket";

/** Back-off between reconnects, in ms. Capped so a long outage does not turn
 * into a ten-minute silence after it ends, and jittered so a hundred cockpits
 * that lost the same API do not all come back in the same millisecond. */
const BACKOFF_MS = [1000, 2000, 5000, 10000, 15000];

function backoffFor(attempt: number): number {
  const base = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)];
  return base + Math.floor(Math.random() * 500);
}

class WsStore {
  private socket: WebSocket | null = null;
  private listeners = new Set<Listener>();
  private statusListeners = new Set<(status: WsStatus) => void>();
  /** Reference-counted per incident id, so two panels watching the same room
   * do not unsubscribe each other when one of them closes. */
  private watched = new Map<string, number>();
  private attempt = 0;
  private retry: ReturnType<typeof setTimeout> | null = null;
  private closing = false;

  status: WsStatus = "disconnected";

  // -- lifecycle ---------------------------------------------------------

  connect(): void {
    if (typeof window === "undefined") return; // server render: nothing to open
    if (this.socket || this.retry) return;
    void this.open();
  }

  /** Close and stay closed. Used when the last subscriber goes away. */
  disconnect(): void {
    this.closing = true;
    if (this.retry) {
      clearTimeout(this.retry);
      this.retry = null;
    }
    const socket = this.socket;
    this.socket = null;
    this.setStatus("disconnected");
    socket?.close();
    this.closing = false;
  }

  private async open(): Promise<void> {
    this.setStatus("connecting");
    let ticket: string;
    try {
      ticket = await this.ticket();
    } catch {
      // No ticket, no socket - and no error on screen either: the caller is
      // still polling, so this is a slower cockpit rather than a broken one.
      this.scheduleRetry();
      return;
    }
    let socket: WebSocket;
    try {
      socket = new WebSocket(WS_URL, [TICKET_SUBPROTOCOL, ticket]);
    } catch {
      this.scheduleRetry();
      return;
    }
    this.socket = socket;

    socket.onopen = () => {
      this.attempt = 0;
      this.setStatus("connected");
      this.sendWatch();
    };
    socket.onmessage = (event) => {
      let frame: WsFrame;
      try {
        frame = JSON.parse(String(event.data)) as WsFrame;
      } catch {
        return; // a frame we cannot parse is one we cannot act on
      }
      for (const listener of Array.from(this.listeners)) listener(frame);
    };
    socket.onclose = () => {
      if (this.socket !== socket) return; // superseded by a newer socket
      this.socket = null;
      this.setStatus("disconnected");
      if (!this.closing && this.listeners.size > 0) this.scheduleRetry();
    };
    socket.onerror = () => {
      // `onclose` always follows, and that is where the retry lives; handling
      // it twice would double the back-off's rate.
    };
  }

  private async ticket(): Promise<string> {
    const response = await fetch(TICKET_PATH, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`ticket refused: ${response.status}`);
    const body = (await response.json()) as { ws_ticket?: string };
    if (!body.ws_ticket) throw new Error("ticket response carried no ticket");
    return body.ws_ticket;
  }

  private scheduleRetry(): void {
    if (this.retry) return;
    const delay = backoffFor(this.attempt++);
    this.retry = setTimeout(() => {
      this.retry = null;
      void this.open();
    }, delay);
  }

  private setStatus(status: WsStatus): void {
    if (this.status === status) return;
    this.status = status;
    for (const listener of Array.from(this.statusListeners)) listener(status);
  }

  // -- subscription ------------------------------------------------------

  /** Every frame. Screens filter; the store does not guess what they want. */
  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    this.connect();
    return () => {
      this.listeners.delete(listener);
      if (this.listeners.size === 0) this.disconnect();
    };
  }

  onStatus(listener: (status: WsStatus) => void): () => void {
    this.statusListeners.add(listener);
    listener(this.status);
    return () => {
      this.statusListeners.delete(listener);
    };
  }

  /** Ask the server for nudges about one incident. Reference-counted. */
  watch(incidentId: string): () => void {
    this.watched.set(incidentId, (this.watched.get(incidentId) ?? 0) + 1);
    this.sendWatch();
    return () => {
      const held = (this.watched.get(incidentId) ?? 1) - 1;
      if (held <= 0) this.watched.delete(incidentId);
      else this.watched.set(incidentId, held);
      this.sendWatch();
    };
  }

  private sendWatch(): void {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return;
    this.socket.send(
      JSON.stringify({
        type: "watch",
        incident_ids: Array.from(this.watched.keys()),
      }),
    );
  }
}

export const wsStore = new WsStore();
