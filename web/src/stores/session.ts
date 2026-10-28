import { defineStore } from "pinia";
import { hub } from "@/api/client";
import { loadAccountPrefs, stopAccountSync } from "./prefs";

export interface ClusterRef { id: number; name: string; permissions: string[] }
export interface HaproxyRef { id: number; name: string; permissions: string[] }
export interface Me {
  user: { id: number; username: string; display_name: string; must_change_password: boolean };
  global_permissions: string[];
  clusters: ClusterRef[];
  haproxy?: HaproxyRef[];        // HAProxy instances this user has a role on, with the permissions there
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
    /** A global permission. HAProxy permissions also count when given on at least one instance (to show the
     *  HAProxy pages); use canHx for one instance and canAll for "on every instance". */
    canGlobal(): (perm: string) => boolean {
      return (perm: string) => Boolean(this.me && (this.me.global_permissions.includes(perm)
        || (perm.startsWith("haproxy.") && (this.me.haproxy || []).some((h) => h.permissions.includes(perm)))));
    },
    canAll(): (perm: string) => boolean {
      return (perm: string) => Boolean(this.me && this.me.global_permissions.includes(perm));
    },
    /** An HAProxy permission on one instance (a role on all of HAProxy, or on that instance). */
    canHx(): (perm: string, haproxyId: number | null | undefined) => boolean {
      return (perm: string, haproxyId: number | null | undefined) => Boolean(this.me && (this.me.global_permissions.includes(perm)
        || (haproxyId != null && (this.me.haproxy || []).find((h) => h.id === haproxyId)?.permissions.includes(perm))));
    },
  },
  actions: {
    async load(): Promise<Me | null> {
      try { this.me = await hub.get<Me>("/auth/me"); } catch { this.me = null; }
      if (this.me && !this.me.user.must_change_password) await loadAccountPrefs();
      this.checked = true;
      this.fixCurrent();
      return this.me;
    },
    async login(username: string, password: string) {
      this.me = await hub.post<Me>("/auth/login", { username, password });
      if (!this.me!.user.must_change_password) await loadAccountPrefs();
      this.checked = true;
      this.fixCurrent();
    },
    async logout() {
      stopAccountSync();
      try { await hub.post("/auth/logout"); } catch { /* already gone */ }
      this.me = null;
    },
    async changePassword(current_password: string, new_password: string) {
      this.me = await hub.post<Me>("/auth/password", { current_password, new_password });
      await loadAccountPrefs();
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
