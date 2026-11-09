// Nginx through the hub: instances, and the reads each instance's k10y agent serves.
import { hub } from "@/api/client";
import type { Level } from "@/lib/format";

export interface NgListen { address: string; port: number | null; ssl: boolean; http2: boolean; default: boolean; text: string }
export interface NgLocation {
  id: string; file: string; line: number; match: string; path: string; modifier: string; kind: string; proxy_pass: string | null;
  upstream: string | null; upstream_known?: boolean; return: string | null; root: string | null; alias: string | null; try_files: string | null;
  rewrites: string[]; allow: string[]; deny: string[]; limit_req: string | null; auth_basic: string | null; websocket: boolean; locations: NgLocation[];
  timeout?: string | null; other?: string[];
}
export interface NgSite {
  id: string; file: string; line: number; context: "http" | "stream"; server_names: string[]; listen: NgListen[]; ssl: boolean;
  ssl_certificate: string | null; kind: string; return: string | null; proxy_pass: string | null; upstream: string | null;
  upstreams: string[]; locations: NgLocation[];
}
export interface NgServer { id: string; line: number; address: string; host: string; port: number | null; weight: number; backup: boolean; down: boolean;
  max_fails: string | null; fail_timeout: string | null; max_conns: string | null }
export interface NgUpstream { id: string; file: string; line: number; context: string; name: string; method: string; method_args: string;
  keepalive: string | null; servers: NgServer[]; used_by: string[]; other?: string[] }
export interface NgConfig { conf: string; files: string[]; errors: { file: string; line: number; message: string }[]; sites: NgSite[]; upstreams: NgUpstream[];
  access_logs: { path: string; format: string }[]; error_logs: string[]; log_formats: Record<string, string> }
export interface NgGroup { req: number; e5_pct: number; e4_pct: number; p95: number }
export type NgPoint = [number, { req: number; e5_pct: number; e4_pct: number; bytes: number; p50: number; p95: number;
  sites: Record<string, NgGroup>; locations: Record<string, NgGroup>; upstreams: Record<string, NgGroup>; servers: Record<string, NgGroup> }];
export interface NgHealth { up: boolean; ms: number; error: string; since: number; checked: number }
export interface NgOverview {
  instance: { id: number; name: string; url: string; group_name: string };
  info: { agent_version: string; name: string; nginx_version: string | null; running: boolean; conf: string; files: string[]; parse_errors: any[];
    access_logs: string[]; log_formats: Record<string, string>; stub_status: string | null; recommended_log_format: string };
  config: NgConfig;
  summary: { sites: number; upstreams: number; servers: number; req: number | null; e5_pct: number | null; p95: number | null; active: number | null;
    problems: { kind: string; message: string; upstream?: string; server?: string; path?: string }[] };
  health: Record<string, NgHealth>;
}

export const ng = {
  list: () => hub.get<any[]>("/nginx"),
  overview: (id: number) => hub.get<NgOverview>(`/nginx/${id}/overview`),
  stats: (id: number, since: number) => hub.get<{ points: NgPoint[]; interval_s: number; stub: any }>(`/nginx/${id}/stats`, { since }),
  logs: (id: number, after: number) => hub.get<{ lines: any[]; formats: Record<string, string>; paths: string[] }>(`/nginx/${id}/logs`, { after }),
  errors: (id: number) => hub.get<{ lines: any[] }>(`/nginx/${id}/errors`),
  certificates: (id: number) => hub.get<any[]>(`/nginx/${id}/certificates`),
  raw: (id: number) => hub.get<{ text: string; check: string }>(`/nginx/${id}/config/raw`),
  file: (id: number, path: string) => hub.get<{ path: string; text: string }>(`/nginx/${id}/files`, { path }),
  change: (id: number, ops: NgOp[], apply: boolean, note = "") => hub.post<NgResult>(`/nginx/${id}/changes`, { ops, apply, note }, 90000),
  history: (id: number) => hub.get<any[]>(`/nginx/${id}/history`),
  version: (id: number, v: number) => hub.get<any>(`/nginx/${id}/history/${v}`),
  restore: (id: number, v: number, apply: boolean) => hub.post<NgResult>(`/nginx/${id}/history/${v}/restore`, { apply }, 90000),
};

export interface NgOp { kind: "site" | "location" | "upstream" | "file"; op: "create" | "update" | "delete"; id?: string; site?: string; path?: string; text?: string; data?: any }
export interface NgError { file: string; line: number; level: string; message: string; text: string }
export interface NgResult { valid: boolean; applied: boolean; diff: string; errors: NgError[]; check: string; summary: string[]; version?: number }

export const serverKey = (s: NgServer) => `${s.host}:${s.port}`;

/** A server's state: down in the config, failing its check, backup, or up. */
export function serverState(s: NgServer, health: Record<string, NgHealth>): { level: Level; label: string } {
  if (s.down) return { level: "neutral", label: "Down (config)" };
  const h = health[serverKey(s)];
  if (h && !h.up) return { level: "critical", label: "Not answering" };
  if (!h) return { level: "neutral", label: "Checking…" };
  return s.backup ? { level: "neutral", label: "Backup" } : { level: "good", label: "Up" };
}

export function upstreamHealth(u: NgUpstream, health: Record<string, NgHealth>): { level: Level; label: string; up: number; total: number } {
  const active = u.servers.filter((s) => !s.down && !s.backup);
  const up = active.filter((s) => health[serverKey(s)]?.up).length;
  const known = active.filter((s) => health[serverKey(s)]).length;
  const level: Level = !active.length ? "neutral" : known < active.length ? "neutral" : up === 0 ? "critical" : up < active.length ? "warning" : "good";
  return { level, label: `${up}/${active.length} up`, up, total: active.length };
}

export const MATCH: Record<string, string> = { exact: "=", prefix: "", "prefix-priority": "^~", regex: "~", "regex-i": "~*", named: "@" };
export const KIND: Record<string, string> = { proxy: "Proxy", static: "Static files", redirect: "Redirect", return: "Fixed answer", other: "Other", mixed: "Mixed", tcp: "TCP" };
export const locationText = (l: NgLocation) => `${MATCH[l.match] ? MATCH[l.match] + " " : ""}${l.path}`;
export const siteName = (s: NgSite) => s.server_names.filter((n) => n !== "_").join(", ") || (s.context === "stream" ? "TCP" : "default");
export const listenText = (s: NgSite) => s.listen.map((l) => `${l.port ?? l.address}${l.ssl ? " TLS" : ""}`).join(", ");
export const rate = (n: number | null | undefined) => (n == null ? "–" : n >= 100 ? n.toFixed(0) : n >= 10 ? n.toFixed(1) : n.toFixed(2).replace(/\.?0+$/, "") || "0");
