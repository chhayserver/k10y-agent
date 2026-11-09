// Selected Nginx instance, its overview and live points; shared by the sidebar picker and the Nginx pages.
import { defineStore } from "pinia";
import { ng, type NgOverview, type NgPoint } from "@/lib/nginx";

const HOUR = 3600e3;
const KEY = "k10y.nginx.instance";

export const useNginx = defineStore("nginx", {
  state: () => ({
    instances: null as any[] | null,
    currentId: null as number | null,
    data: null as NgOverview | null,
    points: [] as NgPoint[],
    error: "",
    updated: 0,
    paused: false,
    loading: false,
    gen: 0,
  }),
  getters: {
    current: (s) => s.instances?.find((i) => i.id === s.currentId) || null,
    last: (s) => s.points[s.points.length - 1]?.[1],
  },
  actions: {
    async loadInstances(select?: number) {
      try { this.instances = await ng.list(); } catch (e: any) { this.instances = []; this.error = e.message; return; }
      let saved: number | null = null;
      try { saved = Number(localStorage.getItem(KEY)) || null; } catch { /* storage blocked */ }
      const ids = this.instances.map((i) => i.id);
      const next = select && ids.includes(select) ? select : this.currentId && ids.includes(this.currentId) ? this.currentId
        : saved && ids.includes(saved) ? saved : ids[0] ?? null;
      if (next !== this.currentId) this.select(next); else if (next != null && !this.data) this.refresh();
    },
    select(id: number | null) {
      this.currentId = id; this.data = null; this.points = []; this.error = ""; this.gen++;
      try { if (id) localStorage.setItem(KEY, String(id)); } catch { /* storage blocked */ }
      this.refresh();
    },
    async refresh() {
      if (this.currentId == null) { this.data = null; return; }
      const id = this.currentId, gen = this.gen;
      this.loading = true;
      try {
        const d = await ng.overview(id);
        if (gen === this.gen) { this.data = d; this.error = ""; this.updated = Date.now(); }
        const since = this.points.length ? this.points[this.points.length - 1][0] : 0;
        const st = await ng.stats(id, since);
        if (gen === this.gen) this.points = this.points.concat(st.points).filter((p) => p[0] >= Date.now() - HOUR);
      } catch (e: any) { if (gen === this.gen) this.error = e.message; }
      finally { if (gen === this.gen) this.loading = false; }
    },
  },
});
