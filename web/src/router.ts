import { createRouter, createWebHistory, type RouteRecordRaw } from "vue-router";
import { NAV, pathOf, systemById, to } from "@/lib/nav";
import { prefs } from "@/stores/prefs";
import { useSession } from "@/stores/session";
import { rememberSystem } from "@/stores/ui";

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
  home: () => import("@/views/Home.vue"),
  events: () => import("@/views/Events.vue"),
  alerts: () => import("@/views/Alerts.vue"),
  activity: () => import("@/views/Activity.vue"),
  users: () => import("@/views/admin/Users.vue"),
  roles: () => import("@/views/admin/Roles.vue"),
  "manage-clusters": () => import("@/views/admin/Clusters.vue"),
  "manage-haproxy": () => import("@/views/admin/HAProxyInstances.vue"),
  system: () => import("@/views/admin/System.vue"),
  settings: () => import("@/views/Settings.vue"),
};

const haproxyView = () => import("@/views/haproxy/HAProxyView.vue");
const component = (id: string) => views[id] || (id.startsWith("haproxy-") ? haproxyView : undefined);

// Old URLs (before systems had their own prefix) keep working.
const legacy: RouteRecordRaw[] = NAV.filter((n) => `/${n.id}` !== pathOf(n)).map((n) => ({ path: `/${n.id}`, redirect: () => pathOf(n) }));

const routes: RouteRecordRaw[] = [
  { path: "/login", component: () => import("@/views/Login.vue"), meta: { bare: true, public: true } },
  { path: "/password", component: () => import("@/views/ChangePassword.vue"), meta: { bare: true } },
  { path: "/", redirect: () => to(prefs.start || "home") },
  ...NAV.map((n) => ({ path: pathOf(n), component: component(n.id)!, meta: { nav: n }, props: n.tab ? { tab: n.tab } : undefined })),
  { path: "/k8s", redirect: () => to("overview") },
  { path: "/haproxy", redirect: () => to("haproxy-overview") },
  ...legacy,
  { path: "/:pathMatch(.*)*", redirect: "/" },
];

const router = createRouter({ history: createWebHistory(), routes });

router.beforeEach(async (target) => {
  const s = useSession();
  if (!s.checked) await s.load();
  if (target.meta.public) return s.me ? "/" : true;
  if (!s.me) return { path: "/login", query: target.fullPath !== "/" ? { next: target.fullPath } : {} };
  if (s.me.user.must_change_password && target.path !== "/password") return "/password";
  const nav = target.meta.nav as (typeof NAV)[number] | undefined;
  const sys = systemById(nav?.system);
  if (sys && !sys.visible(s)) return to("home");
  if (nav?.global && !s.canGlobal(nav.global)) return to(sys ? "home" : "clusters");
  if (nav?.cluster && !s.current) return to("clusters");
  if (nav?.system) rememberSystem(nav.system);
  return true;
});

export default router;
