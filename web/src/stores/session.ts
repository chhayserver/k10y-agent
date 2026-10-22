import { defineStore } from "pinia";
import { hub } from "@/api/client";

export interface ClusterRef { id: number; name: string; permissions: string[] }
export interface Me {
  user: { id: number; username: string; display_name: string; must_change_password: boolean };
  global_permissions: string[];
  clusters: ClusterRef[];
  version: string;
}

const CURRENT_KEY = "k10y-current-cluster";

export const useSession = defineStore("session", {
  state: () => ({
    me: null as Me | null,
    checked: false,
    currentId: Number(localStorage.getItem(CURRENT_KEY)) || null as number | null,
  }),
  getters: {
    clusters: (s): ClusterRef[] => s.me?.clusters || [],
    current(): ClusterRef | null {
      return this.clusters.find((c) => c.id === this.currentId) || this.clusters[0] || null;
    },
    /** Permission on the current cluster (cluster permissions) or globally (global permissions). */
    can(): (perm: string, clusterId?: number) => boolean {
      return (perm: string, clusterId?: number) => {
        if (!this.me) return false;
        if (this.me.global_permissions.includes(perm)) return true;
        const c = clusterId != null ? this.clusters.find((x) => x.id === clusterId) : this.current;
        return Boolean(c && c.permissions.includes(perm));
      };
    },
    canGlobal(): (perm: string) => boolean {
      return (perm: string) => Boolean(this.me && this.me.global_permissions.includes(perm));
    },
  },
  actions: {
    async load(): Promise<Me | null> {
      try { this.me = await hub.get<Me>("/auth/me"); } catch { this.me = null; }
      this.checked = true;
      this.fixCurrent();
      return this.me;
    },
    async login(username: string, password: string) {
      this.me = await hub.post<Me>("/auth/login", { username, password });
      this.checked = true;
      this.fixCurrent();
    },
    async logout() {
      try { await hub.post("/auth/logout"); } catch { /* already gone */ }
      this.me = null;
    },
    async changePassword(current_password: string, new_password: string) {
      this.me = await hub.post<Me>("/auth/password", { current_password, new_password });
    },
    setCluster(id: number) {
      this.currentId = id;
      localStorage.setItem(CURRENT_KEY, String(id));
    },
    fixCurrent() {
      if (this.me && this.current && this.current.id !== this.currentId) this.setCluster(this.current.id);
    },
  },
});
