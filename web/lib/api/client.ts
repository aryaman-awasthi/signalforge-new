/**
 * Typed API client - the only path to the SignalForge API (CLAUDE.md web rules).
 * Three clients from one core:
 *  - api: direct dev calls to the API (health page)
 *  - authApi: same-origin /api/auth route handlers (cookie login/logout)
 *  - proxyApi: same-origin /api/proxy - the auth cookie proxy that attaches
 *    the bearer token server-side; browser JS never sees a token.
 */

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number | null,
    public readonly detail?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * The API's `detail` as one sentence. The platform's own errors send a string;
 * a request-body validation failure (422) sends the framework's list of
 * `{loc, msg}` objects, which rendered as "[object Object]" and told nobody
 * which field was wrong.
 */
export function describeDetail(detail: unknown): string | undefined {
  if (detail === undefined || detail === null) return undefined;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const sentences = detail.map((item) => {
      if (item && typeof item === "object" && "msg" in item) {
        const { loc, msg } = item as { loc?: unknown[]; msg?: unknown };
        const field = Array.isArray(loc)
          ? loc.filter((part) => part !== "body" && part !== "query").join(".")
          : "";
        return field ? `${field}: ${String(msg)}` : String(msg);
      }
      return typeof item === "string" ? item : JSON.stringify(item);
    });
    return sentences.join("; ") || undefined;
  }
  return JSON.stringify(detail);
}

function makeClient(base: string) {
  async function request<T>(path: string, init?: RequestInit): Promise<T> {
    let response: Response;
    try {
      response = await fetch(`${base}${path}`, {
        ...init,
        headers: { "Content-Type": "application/json", ...init?.headers },
      });
    } catch {
      throw new ApiError("API unreachable", null);
    }
    if (!response.ok) {
      let detail: string | undefined;
      try {
        detail = describeDetail(((await response.json()) as { detail?: unknown }).detail);
      } catch {
        /* non-JSON error body */
      }
      throw new ApiError(
        detail ?? `API responded ${response.status}`,
        response.status,
        detail,
      );
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  return {
    get: <T>(path: string) => request<T>(path),
    post: <T>(path: string, body?: unknown) =>
      request<T>(path, {
        method: "POST",
        body: body === undefined ? undefined : JSON.stringify(body),
      }),
    put: <T>(path: string, body: unknown) =>
      request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
    patch: <T>(path: string, body: unknown) =>
      request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
    del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  };
}

export const api = makeClient(API_BASE_URL);
export const authApi = makeClient("/api/auth");
export const proxyApi = makeClient("/api/proxy");
