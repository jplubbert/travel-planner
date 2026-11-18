// Thin HTTP client. The test identity travels in X-User-Id; the backend enforces roles,
// so errors (403, 409…) are surfaced exactly as the API returns them.

export const API_BASE = "/api";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: unknown = null,
    readonly requestId: string | null = null,
  ) {
    super(message);
  }
}

type Body = { code?: string; message?: string; details?: unknown; requestId?: string | null };

export async function toApiError(response: Response): Promise<ApiError> {
  let body: Body = {};
  try {
    body = (await response.json()) as Body;
  } catch {
    // non-JSON error (proxy, network): keep the status
  }
  return new ApiError(response.status, body.code ?? `HTTP_${response.status}`,
    body.message ?? response.statusText, body.details ?? null, body.requestId ?? null);
}

export function createApi(userId: string, fetchImpl: typeof fetch = fetch) {
  async function raw(method: string, path: string, body?: unknown): Promise<Response> {
    const headers: Record<string, string> = { "X-User-Id": userId };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    return fetchImpl(`${API_BASE}${path}`, {
      method, headers, body: body === undefined ? undefined : JSON.stringify(body),
    });
  }

  async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const response = await raw(method, path, body);
    if (!response.ok) throw await toApiError(response);
    return (response.status === 204 ? undefined : await response.json()) as T;
  }

  return {
    userId,
    raw,
    get: <T>(path: string) => request<T>("GET", path),
    post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
    put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body ?? {}),
    patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body ?? {}),
    del: <T>(path: string) => request<T>("DELETE", path),
    // A failed search is still a full search result (with 502/503/504): show it as such.
    async search<T extends { outcome: string }>(path: string, body: unknown): Promise<T> {
      const response = await raw("POST", path, body);
      if (response.ok) return (await response.json()) as T;
      const copy = response.clone();
      try {
        const parsed = (await response.json()) as Partial<T>;
        if (parsed && parsed.outcome === "failed") return parsed as T;
      } catch {
        // fall through
      }
      throw await toApiError(copy);
    },
  };
}

export type Api = ReturnType<typeof createApi>;
