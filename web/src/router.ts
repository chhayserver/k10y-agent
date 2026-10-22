import { createRouter, createWebHistory, type RouteRecordRaw } from "vue-router";
import { NAV } from "@/lib/nav";
import { prefs } from "@/stores/prefs";
import { useSession } from "@/stores/session";

const views: Record<string, () => Promise<any>> = {
  clusters: () => import("@/views/Fleet.vue"),
  overview: () => import("@/views/Overview.vue"),
  map: () => import("@/views/MapView.vue"),
  ai: () => import("@/views/AiView.vue"),
  nodes: () => import("@/views/Nodes.vue"),
  namespaces: () => import("@/views/Namespaces.vue"),
  deployments: () => import("@/views/Deployments.vue"),
  statefulsets: () => import("@/views/StatefulSets.vue"),
  daemonsets: () => import("@/views/DaemonSets.vue"),
  jobs: () => import("@/views/Jobs.vue"),
  cronjobs: () => import("@/views/CronJobs.vue"),
  hpas: () => import("@/views/Hpas.vue"),
  ingresses: () => import("@/views/Ingresses.vue"),
  storage: () => import("@/views/Storage.vue"),
  pods: () => import("@/views/Pods.vue"),
  services: () => import("@/views/Services.vue"),
  configmaps: () => import("@/views/ConfigMaps.vue"),
  secrets: () => import("@/views/Secrets.vue"),
  events: () => import("@/views/Events.vue"),
  alerts: () => import("@/views/Alerts.vue"),
  activity: () => import("@/views/Activity.vue"),
  users: () => import("@/views/admin/Users.vue"),
  roles: () => import("@/views/admin/Roles.vue"),
  "manage-clusters": () => import("@/views/admin/Clusters.vue"),
  system: () => import("@/views/admin/System.vue"),
  settings: () => import("@/views/Settings.vue"),
};

const routes: RouteRecordRaw[] = [
  { path: "/login", component: () => import("@/views/Login.vue"), meta: { bare: true, public: true } },
  { path: "/password", component: () => import("@/views/ChangePassword.vue"), meta: { bare: true } },
  { path: "/", redirect: () => `/${prefs.start || "overview"}` },
  ...NAV.map((n) => ({ path: `/${n.id}`, component: views[n.id], meta: { nav: n } })),
  { path: "/:pathMatch(.*)*", redirect: "/" },
];

const router = createRouter({ history: createWebHistory(), routes });

router.beforeEach(async (to) => {
  const s = useSession();
  if (!s.checked) await s.load();
  if (to.meta.public) return s.me ? "/" : true;
  if (!s.me) return { path: "/login", query: to.fullPath !== "/" ? { next: to.fullPath } : {} };
  if (s.me.user.must_change_password && to.path !== "/password") return "/password";
  const nav = to.meta.nav as (typeof NAV)[number] | undefined;
  if (nav?.global && !s.canGlobal(nav.global)) return "/clusters";
  if (nav?.cluster && !s.current) return "/clusters";
  return true;
});

export default router;
