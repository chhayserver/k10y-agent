// Chart ranges for Kubernetes usage. Up to an hour comes from the agent (live, every ~15 s); longer ranges come
// from the hub, which keeps 5-minute averages for 7 days. Points are returned as [t, cpu_m, memory_bytes].
import { onBeforeUnmount, ref, watch, type Ref } from "vue";
import { hub } from "@/api/client";

export const RANGES = { "5m": 5 * 60e3, "15m": 15 * 60e3, "1h": 60 * 60e3, "6h": 6 * 3600e3, "24h": 86400e3, "7d": 7 * 86400e3 } as const;
export type RangeKey = keyof typeof RANGES;
export const isLong = (k: string) => (RANGES[k as RangeKey] || 0) > 3600e3;

export function useLongHistory(clusterId: () => number | undefined, range: () => string, scope: "cluster" | "node" | "pod",
                               params: () => { name?: string; namespace?: string } = () => ({})): Ref<number[][] | null> {
  const points = ref<number[][] | null>(null);
  let timer = 0;
  async function load() {
    const cid = clusterId();
    if (!cid || !isLong(range())) return;
    try {
      const r = await hub.get<{ points: number[][] }>(`/clusters/${cid}/history`, { scope, since: Date.now() - RANGES[range() as RangeKey], ...params() });
      points.value = r.points.map((p) => [p[0], p[1], p[2]]);
    } catch { points.value = points.value || []; }
  }
  watch([clusterId, range], () => { clearInterval(timer); points.value = null; if (isLong(range())) { load(); timer = window.setInterval(load, 60000); } }, { immediate: true });
  onBeforeUnmount(() => clearInterval(timer));
  return points;
}
