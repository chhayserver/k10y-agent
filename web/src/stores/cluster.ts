// Data for the selected cluster, refreshed on an interval, plus live CPU/memory history.
import { defineStore } from "pinia";
import { agent } from "@/api/client";
import { useSession } from "./session";

export type Point = [number, number, number];
export interface Info { cluster: string; agent_version: string; kubernetes_version: string; control_enabled: boolean; protected_namespaces: string[] }

const HOUR = 3600e3;

export const useCluster = defineStore("cluster", {
  state: () => ({
    info: null as Info | null,
    data: null as Record<string, any> | null,
    error: "" as string,
    lastUpdated: 0,
    loading: false,
    paused: false,
    gen: 0,
    metrics: {} as Record<string, any>,
    liveError: "" as string,
  }),
  getters: {
    control: (s) => Boolean(s.info?.control_enabled),
    isProtected: (s) => (ns?: string | null) => Boolean(ns && s.info?.protected_namespaces.includes(ns)),
  },
  actions: {
    reset() {
      this.gen += 1;
      this.info = null; this.data = null; this.error = ""; this.lastUpdated = 0; this.metrics = {}; this.loading = false;
    },
    async refresh() {
      const session = useSession();
      const c = session.current;
      if (!c || this.loading) return;
      const gen = this.gen, a = agent(c.id), can = (p: string) => session.can(p);
      this.loading = true;
      try {
        if (!this.info) this.info = await a.get<Info>("info");
        const opt = <T>(p: Promise<T>, fallback: T) => p.catch(() => fallback);
        const [summary, nodes, pods, deployments, services, events, statefulsets, daemonsets, jobs, cronjobs, ingresses, pvcs, pvs, hpas] = await Promise.all([
          a.get("summary"), a.get("nodes"), a.get("pods"), a.get("deployments"), a.get("services"),
          a.get("events", { limit: 300 }), opt(a.get("statefulsets"), []), opt(a.get("daemonsets"), []), opt(a.get("jobs"), []),
          // added in agent 2.0; older agents simply show empty pages
          opt(a.get("cronjobs"), []), opt(a.get("ingresses"), []), opt(a.get("persistentvolumeclaims"), []),
          opt(a.get("persistentvolumes"), []), opt(a.get("hpas"), []),
        ]);
        if (gen !== this.gen) return;
        this.data = { summary, nodes, pods, deployments, services, events, statefulsets, daemonsets, jobs, cronjobs, ingresses, pvcs, pvs, hpas,
          canSecrets: can("secrets.view") };
        this.error = "";
        this.lastUpdated = Date.now();
      } catch (e: any) {
        if (gen === this.gen) this.error = `${c.name}: ${e.message}`;
      } finally {
        if (gen === this.gen) this.loading = false;
      }
    },
    /** Incrementally fetch CPU/memory history: scope cluster | nodes | pod. */
    async loadMetrics(scope: "cluster" | "nodes" | "pod", extra: Record<string, string> = {}) {
      const c = useSession().current;
      if (!c) return null;
      const key = scope === "pod" ? `pod/${extra.namespace}/${extra.name}` : scope;
      const prev = this.metrics[key];
      const lastT = (pts?: Point[]) => (pts && pts.length ? pts[pts.length - 1][0] : 0);
      let since = 0;
      if (prev) since = scope === "nodes" ? Math.min(...Object.values<any>(prev.nodes).map((n) => lastT(n.points)), Infinity) : lastT(prev.points);
      if (!Number.isFinite(since)) since = 0;
      const gen = this.gen;
      const res = await agent(c.id).get("metrics/history", { scope, since, ...extra }, 8000);
      if (gen !== this.gen) return null;
      const cutoff = Date.now() - HOUR;
      const merge = (a: Point[] | undefined, b: Point[]) => { const l = lastT(a); return (a || []).concat(b.filter((p) => p[0] > l)).filter((p) => p[0] >= cutoff); };
      if (scope === "nodes") {
        const nodes: Record<string, any> = {};
        for (const [n, v] of Object.entries<any>(res.nodes)) nodes[n] = { ...v, points: merge(prev?.nodes?.[n]?.points, v.points) };
        this.metrics[key] = { ...res, nodes };
      } else {
        this.metrics[key] = { ...res, points: merge(prev?.points, res.points) };
      }
      this.liveError = res.error || "";
      return this.metrics[key];
    },
  },
});
