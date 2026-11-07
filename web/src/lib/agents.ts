// Which clusters run an agent older than the hub, and what that costs. Checked by the hub (cached 5 minutes).
import { reactive } from "vue";
import { hub } from "@/api/client";

export interface AgentStatus { id: number; name: string; agent_version: string | null; outdated: boolean; missing: string[]; error: string }
export const agents = reactive<{ byId: Record<number, AgentStatus>; hub: string; updateUrl: string; loaded: boolean }>({
  byId: {}, hub: "", updateUrl: "", loaded: false });
let pending: Promise<void> | null = null;

export function loadAgents(fresh = false): Promise<void> {
  if (pending && !fresh) return pending;
  pending = hub.get<{ hub_version: string; update_url: string; clusters: AgentStatus[] }>("/clusters/agents", fresh ? { fresh: true } : undefined)
    .then((r) => { agents.byId = Object.fromEntries(r.clusters.map((c) => [c.id, c])); agents.hub = r.hub_version; agents.updateUrl = r.update_url; agents.loaded = true; })
    .catch(() => { /* older hub or no access: no badges */ })
    .finally(() => { setTimeout(() => (pending = null), 60_000); });
  return pending;
}

/** Tooltip text: what's missing and how to update. */
export function outdatedHint(a: AgentStatus | undefined): string {
  if (!a?.outdated) return "";
  const list = a.missing.length ? `Missing: ${a.missing.join(", ")}.` : "Update it to get the latest fixes.";
  return `Agent ${a.agent_version} is older than the hub (${agents.hub}). ${list} Update: kubectl apply -f ${agents.updateUrl}`;
}
