// Shared types and helpers for the HAProxy pages.
import { hub } from "@/api/client";
import type { Level } from "./format";

export interface HxStats { [k: string]: any }
export interface HxBind { address: string; port: number; ssl: boolean; name: string }
export interface HxRoute { kind: "path_prefix" | "path" | "host" | "other"; value: string; backend: string }
export interface HxFrontend { name: string; mode: string; binds: HxBind[]; default_backend: string | null; routes: HxRoute[]; simple_routes: boolean; stats_page: boolean; stats: HxStats }
export interface HxServer { name: string; address: string; port: number; weight: number; check: boolean; backup: boolean; state: string; stats: HxStats }
export interface HxBackend { name: string; mode: string; balance: string; check: { type: string; uri: string }; servers: HxServer[]; used_by: string[]; stats: HxStats }
export interface HxOverview { instance: any; frontends: HxFrontend[]; backends: HxBackend[]; config_version: number; keeps_server_state: boolean }
export interface HxOp { op: "create" | "update" | "delete" | "raw"; kind: "frontend" | "backend" | "server" | "config"; name?: string; backend?: string; data?: Record<string, any> }

export const STATE: Record<string, { level: Level; label: string }> = {
  up: { level: "good", label: "Up" }, down: { level: "critical", label: "Down" }, drain: { level: "warning", label: "Draining" },
  maint: { level: "neutral", label: "Maintenance" }, nocheck: { level: "neutral", label: "No check" }, unknown: { level: "neutral", label: "Unknown" },
};
export const ROUTE_KIND: Record<string, string> = { path_prefix: "Path starts with", path: "Path is", host: "Host is", other: "Rule" };
export const BALANCE = [["roundrobin", "Round robin"], ["leastconn", "Least connections"], ["source", "Source IP (sticky)"], ["static-rr", "Static round robin"], ["first", "First available"], ["uri", "URI hash"]];

export const bindText = (b: HxBind) => `${b.address === "*" ? "*" : b.address}:${b.port}${b.ssl ? " (TLS)" : ""}`;
export function routeText(r: HxRoute) { return r.kind === "other" ? r.value : `${r.kind === "host" ? "" : ""}${r.value}`; }
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
  changes: (id: number, ops: HxOp[], apply: boolean) => hub.post<{ diff: string; valid: boolean; error: string; applied: boolean; summary: string[] }>(`/haproxy/${id}/changes`, { ops, apply }, 60000),
};
