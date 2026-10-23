// Shared types and helpers for the HAProxy pages.
import { hub } from "@/api/client";
import type { Level } from "./format";

export interface HxStats { [k: string]: any }
export interface HxBind { address: string; port: number; ssl: boolean; name: string; certificate?: string }
export interface HxRoute { kind: "path_prefix" | "path" | "host" | "header" | "src" | "other"; value: string; backend: string; header?: string }
export interface HxPolicies { allow_ips: string[]; redirect_https: boolean; rate_limit: { requests: number; period: number } | null; custom_rules: number }
export interface HxFrontend { name: string; mode: string; binds: HxBind[]; default_backend: string | null; routes: HxRoute[]; simple_routes: boolean; stats_page: boolean; stats: HxStats; policies: HxPolicies }
export interface HxSync { id: number; backend: string; cluster_id: number; namespace: string; service: string; port: string; mode: string; enabled: boolean; last_result: string; last_error: string; last_sync_at: string | null }
export interface HxServer { name: string; address: string; port: number; weight: number; check: boolean; backup: boolean; state: string; stats: HxStats }
export interface HxBackend { name: string; mode: string; balance: string; check: { type: string; uri: string }; servers: HxServer[]; used_by: string[]; stats: HxStats; k8s_sync: HxSync | null }
export interface HxOverview { instance: any; group: any[]; frontends: HxFrontend[]; backends: HxBackend[]; config_version: number; keeps_server_state: boolean }
export type HxPoint = [number, { f: Record<string, { req: number; sess: number; slim: number; e5: number; e5_pct: number; in: number; out: number }>; b: Record<string, { req: number; sess: number; rtime: number; queue: number; e5: number }> }];
export interface HxOp { op: "create" | "update" | "delete" | "raw"; kind: "frontend" | "backend" | "server" | "config"; name?: string; backend?: string; data?: Record<string, any> }

export const STATE: Record<string, { level: Level; label: string }> = {
  up: { level: "good", label: "Up" }, down: { level: "critical", label: "Down" }, drain: { level: "warning", label: "Draining" },
  maint: { level: "neutral", label: "Maintenance" }, nocheck: { level: "neutral", label: "No check" }, unknown: { level: "neutral", label: "Unknown" },
};
export const ROUTE_KIND: Record<string, string> = { path_prefix: "Path starts with", path: "Path is", host: "Host is", header: "Header", src: "Client IP in", other: "Rule" };
export const BALANCE = [["roundrobin", "Round robin"], ["leastconn", "Least connections"], ["source", "Source IP (sticky)"], ["static-rr", "Static round robin"], ["first", "First available"], ["uri", "URI hash"]];

export const bindText = (b: HxBind) => `${b.address === "*" ? "*" : b.address}:${b.port}${b.ssl ? " (TLS)" : ""}`;
export function routeText(r: HxRoute) { return r.kind === "header" ? `${r.header}: ${r.value}` : r.value; }
export function backendHealth(b: HxBackend): { level: Level; label: string; up: number; total: number } {
  const active = b.servers.filter((s) => !s.backup), up = active.filter((s) => s.state === "up" || s.state === "nocheck").length, total = active.length;
  if (!total) return { level: "neutral", label: "No servers", up, total };
  if (up === 0) return { level: "critical", label: "All down", up, total };
  if (up < total) return { level: "warning", label: `${up}/${total} up`, up, total };
  return { level: "good", label: `${up}/${total} up`, up, total };
}
export const num = (v: any) => (v == null || v === "" ? "–" : Number(v).toLocaleString());

export const hx = {
  list: () => hub.get<any[]>("/haproxy"),
  overview: (id: number) => hub.get<HxOverview>(`/haproxy/${id}/overview`),
  config: (id: number) => hub.get<{ text: string; version: number }>(`/haproxy/${id}/config`),
  setState: (id: number, backend: string, server: string, state: string) => hub.put(`/haproxy/${id}/servers/${encodeURIComponent(backend)}/${encodeURIComponent(server)}/state`, { state }),
  changes: (id: number, ops: HxOp[], apply: boolean, note = "") => hub.post<HxPlan>(`/haproxy/${id}/changes`, { ops, apply, note }, 60000),
  groupChanges: (ids: number[], ops: HxOp[], apply: boolean, note = "") => hub.post<{ results: (HxPlan & { instance_id: number; name: string })[] }>("/haproxy/group-changes", { instance_ids: ids, ops, apply, note }, 120000),
  history: (id: number, since: number) => hub.get<{ points: HxPoint[]; interval_s: number }>(`/haproxy/${id}/history`, { since }),
  versions: (id: number) => hub.get<any[]>(`/haproxy/${id}/versions`),
  version: (id: number, vid: number) => hub.get<{ id: number; version: number; text: string }>(`/haproxy/${id}/versions/${vid}`),
  certificates: (id: number) => hub.get<any[]>(`/haproxy/${id}/certificates`),
  uploadCert: (id: number, name: string, pem: string) => hub.post(`/haproxy/${id}/certificates`, { name, pem }, 60000),
  deleteCert: (id: number, name: string) => hub.del(`/haproxy/${id}/certificates/${encodeURIComponent(name)}`),
  stickTables: (id: number) => hub.get<any[]>(`/haproxy/${id}/stick-tables`),
  stickEntries: (id: number, table: string) => hub.get<any[]>(`/haproxy/${id}/stick-tables/${encodeURIComponent(table)}`),
  logs: (id: number, after: number) => hub.get<{ enabled: boolean; port: number | null; tag: string; lines: any[]; other_tags: string[] }>(`/haproxy/${id}/logs`, { after }),
  setSync: (id: number, backend: string, body: any) => hub.put<HxSync & { message: string }>(`/haproxy/${id}/k8s-sync/${encodeURIComponent(backend)}`, body),
  runSync: (id: number, backend: string) => hub.post<HxSync & { message: string }>(`/haproxy/${id}/k8s-sync/${encodeURIComponent(backend)}/run`),
  deleteSync: (id: number, backend: string) => hub.del(`/haproxy/${id}/k8s-sync/${encodeURIComponent(backend)}`),
};

export interface HxPlan { diff: string; valid: boolean; error: string; applied: boolean; summary: string[] }
