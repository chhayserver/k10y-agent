// Selected HAProxy instance and its live overview, shared by the sidebar picker and the HAProxy pages.
import { defineStore } from "pinia";
import { hx, type HxOverview, type HxPoint } from "@/lib/haproxy";

const HOUR = 3600e3;

const KEY = "k10y.haproxy.instance";

export const useHaproxy = defineStore("haproxy", {
  state: () => ({
    instances: null as any[] | null,
    currentId: null as number | null,
    data: null as HxOverview | null,
    error: "",
    updated: 0,
    paused: false,
    loading: false,
    gen: 0,
    points: [] as HxPoint[],
  }),
  getters: {
    current: (s) => s.instances?.find((i) => i.id === s.currentId) || null,
  },
  actions: {
    async loadInstances(select?: number) {
      try { this.instances = await hx.list(); } catch (e: any) { this.instances = []; this.error = e.message; return; }
      let saved: number | null = null;
      try { saved = Number(localStorage.getItem(KEY)) || null; } catch { /* storage blocked */ }
      const ids = this.instances.map((i) => i.id);
      const next = select && ids.includes(select) ? select : this.currentId && ids.includes(this.currentId) ? this.currentId
        : saved && ids.includes(saved) ? saved : ids[0] ?? null;
      if (next !== this.currentId) this.select(next);
    },
    select(id: number | null) {
      this.currentId = id; this.data = null; this.error = ""; this.points = []; this.gen++;
      try { if (id) localStorage.setItem(KEY, String(id)); } catch { /* storage blocked */ }
      this.refresh();
    },
    async refresh() {
      if (this.currentId == null) { this.data = null; return; }
      const id = this.currentId, gen = this.gen;
      this.loading = true;
      try {
        const d = await hx.overview(id);
        if (gen === this.gen) { this.data = d; this.error = ""; this.updated = Date.now(); }
        const since = this.points.length ? this.points[this.points.length - 1][0] : 0;
        const hist = await hx.history(id, since);
        if (gen === this.gen) this.points = this.points.concat(hist.points).filter((p) => p[0] >= Date.now() - HOUR);
      } catch (e: any) { if (gen === this.gen) this.error = e.message; }
      finally { if (gen === this.gen) this.loading = false; }
    },
  },
});
