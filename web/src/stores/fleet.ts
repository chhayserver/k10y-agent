// Health summary of every cluster the user can see (cluster switcher dots, All clusters page).
import { reactive } from "vue";
import { agent } from "@/api/client";
import { useSession } from "./session";

export const fleet = reactive<Record<number, { summary?: any; error?: string; at: number }>>({});
export async function refreshFleet() {
  await Promise.all(useSession().clusters.map(async (c) => {
    try { fleet[c.id] = { summary: await agent(c.id).get("summary", undefined, 10000), at: Date.now() }; }
    catch (e: any) { fleet[c.id] = { error: e.message, at: Date.now() }; }
  }));
}
export function fleetLevel(id: number): string {
  const f = fleet[id];
  if (!f) return "";
  if (f.error) return "critical";
  return ({ healthy: "good", degraded: "warning", critical: "critical" } as Record<string, string>)[f.summary.health] || "";
}
