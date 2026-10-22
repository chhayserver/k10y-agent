// HTTP client for the hub. The browser never talks to agents directly: cluster calls go
// through /api/hub/clusters/{id}/api/..., where the hub checks permissions and audits.

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

type Query = Record<string, string | number | boolean | null | undefined>;
interface Opts { method?: string; body?: unknown; query?: Query; text?: boolean; timeout?: number }

let onUnauthorized: (() => void) | null = null;
let onPasswordRequired: (() => void) | null = null;
export function setAuthHandlers(unauth: () => void, password: () => void) { onUnauthorized = unauth; onPasswordRequired = password; }

function qs(query?: Query): string {
  if (!query) return "";
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) if (v !== null && v !== undefined && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}

export async function request<T = any>(path: string, opts: Opts = {}): Promise<T> {
  const method = opts.method || "GET";
  const headers: Record<string, string> = {};
  if (method !== "GET") headers["X-K10Y-CSRF"] = "1";
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), opts.timeout || 30000);
  let r: Response;
  try {
    r = await fetch(`/api/hub${path}${qs(opts.query)}`, {
      method, headers, credentials: "same-origin", signal: ctl.signal,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
  } catch (e: any) {
    throw new ApiError(0, e?.name === "AbortError" ? "The request timed out." : "Cannot reach the k10y hub.");
  } finally {
    clearTimeout(timer);
  }
  if (!r.ok) {
    let msg = `Request failed (HTTP ${r.status})`;
    try {
      const d = (await r.json()).detail;
      if (Array.isArray(d)) msg = d.map((x: any) => `${(x.loc || []).slice(-1)[0]}: ${x.msg}`).join("; ");
      else if (d) msg = String(d);
    } catch { /* not JSON */ }
    if (r.status === 401 && onUnauthorized && !path.startsWith("/auth/login")) onUnauthorized();
    if (r.status === 403 && msg === "password change required" && onPasswordRequired) onPasswordRequired();
    throw new ApiError(r.status, msg);
  }
  return (opts.text ? r.text() : r.json()) as Promise<T>;
}

export const hub = {
  get: <T = any>(path: string, query?: Query) => request<T>(path, { query }),
  post: <T = any>(path: string, body?: unknown, timeout?: number) => request<T>(path, { method: "POST", body: body ?? {}, timeout }),
  patch: <T = any>(path: string, body: unknown) => request<T>(path, { method: "PATCH", body }),
  del: <T = any>(path: string, query?: Query) => request<T>(path, { method: "DELETE", query }),
};

/** Calls to one cluster's agent API (proxied and permission-checked by the hub). */
export function agent(clusterId: number) {
  const base = `/clusters/${clusterId}/api`;
  return {
    get: <T = any>(path: string, query?: Query, timeout?: number) => request<T>(`${base}/${path}`, { query, timeout }),
    text: (path: string, query?: Query) => request<string>(`${base}/${path}`, { query, text: true }),
    post: <T = any>(path: string, body?: unknown) => request<T>(`${base}/${path}`, { method: "POST", body: body ?? {} }),
    del: <T = any>(path: string, query?: Query) => request<T>(`${base}/${path}`, { method: "DELETE", query }),
  };
}
