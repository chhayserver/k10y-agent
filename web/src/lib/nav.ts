// Systems and pages. To add a new system: add it to SYSTEMS, list its pages in NAV with `system`,
// and (if it has instances) give it a scope switcher in AppShell. The sidebar, URLs, route guards,
// "My settings" page list and Home all follow from these two lists.
import type { useSession } from "@/stores/session";

type Session = ReturnType<typeof useSession>;

export interface SystemDef {
  id: string;
  label: string;
  icon: string;
  base: string;                       // URL prefix
  description: string;
  visible: (s: Session) => boolean;   // whether the user can see this system at all
}

export const SYSTEMS: SystemDef[] = [
  { id: "kubernetes", label: "Kubernetes", icon: "logo", base: "/k8s", description: "Clusters, workloads, logs and actions",
    visible: (s) => s.clusters.length > 0 || s.canGlobal("clusters.manage") },
  { id: "haproxy", label: "HAProxy", icon: "lb", base: "/haproxy", description: "Load balancers: services, backends, servers",
    visible: (s) => s.canGlobal("haproxy.view") },
];
export const systemById = (id: string | undefined) => SYSTEMS.find((x) => x.id === id);

// system: the page belongs to that system (shown when it's selected). No system: shared hub page.
// perm: needed on the current cluster; global: needed on all clusters; cluster: page needs a selected cluster;
// slug: URL segment (defaults to id); tab: passed to the page component (one component, several pages).
export interface NavItem {
  id: string; label: string; icon: string; section: string; system?: string; slug?: string; tab?: string;
  perm?: string; global?: string; locked?: boolean; cluster?: boolean; hidden?: boolean;
}
const K = "kubernetes", H = "haproxy";
export const NAV: NavItem[] = [
  { id: "home", label: "Home", icon: "overview", section: "Hub", locked: true, hidden: true },

  { id: "clusters", label: "All clusters", icon: "globe", section: "Clusters", system: K },
  { id: "overview", label: "Overview", icon: "overview", section: "Cluster", system: K, perm: "cluster.view", cluster: true },
  { id: "map", label: "Map", icon: "map", section: "Cluster", system: K, perm: "cluster.view", cluster: true },
  { id: "ai", label: "AI Assistant", icon: "ai", section: "Cluster", system: K, perm: "ai.use", cluster: true },
  { id: "nodes", label: "Nodes", icon: "nodes", section: "Cluster", system: K, perm: "cluster.view", cluster: true },
  { id: "namespaces", label: "Namespaces", icon: "folder", section: "Cluster", system: K, perm: "cluster.view", cluster: true },
  { id: "deployments", label: "Deployments", icon: "deploy", section: "Workloads", system: K, perm: "cluster.view", cluster: true },
  { id: "statefulsets", label: "StatefulSets", icon: "db", section: "Workloads", system: K, perm: "cluster.view", cluster: true },
  { id: "daemonsets", label: "DaemonSets", icon: "nodes", section: "Workloads", system: K, perm: "cluster.view", cluster: true },
  { id: "jobs", label: "Jobs", icon: "pending", section: "Workloads", system: K, perm: "cluster.view", cluster: true },
  { id: "cronjobs", label: "CronJobs", icon: "pending", section: "Workloads", system: K, perm: "cluster.view", cluster: true },
  { id: "hpas", label: "Autoscalers", icon: "scale", section: "Workloads", system: K, perm: "cluster.view", cluster: true },
  { id: "pods", label: "Pods", icon: "pods", section: "Workloads", system: K, perm: "cluster.view", cluster: true },
  { id: "services", label: "Services", icon: "service", section: "Network & config", system: K, perm: "cluster.view", cluster: true },
  { id: "ingresses", label: "Ingresses", icon: "globe", section: "Network & config", system: K, perm: "cluster.view", cluster: true },
  { id: "storage", label: "Storage", icon: "disk", section: "Network & config", system: K, perm: "cluster.view", cluster: true },
  { id: "configmaps", label: "ConfigMaps", icon: "file", section: "Network & config", system: K, perm: "cluster.view", cluster: true },
  { id: "secrets", label: "Secrets", icon: "lock", section: "Network & config", system: K, perm: "secrets.view", cluster: true },
  { id: "events", label: "Events", icon: "events", section: "Activity", system: K, perm: "cluster.view", cluster: true },

  { id: "haproxy-overview", label: "Overview", icon: "overview", section: "Load balancer", system: H, slug: "overview", tab: "overview", global: "haproxy.view" },
  { id: "haproxy-services", label: "Services", icon: "globe", section: "Load balancer", system: H, slug: "services", tab: "services", global: "haproxy.view" },
  { id: "haproxy-backends", label: "Backends", icon: "nodes", section: "Load balancer", system: H, slug: "backends", tab: "backends", global: "haproxy.view" },
  { id: "haproxy-topology", label: "Topology", icon: "map", section: "Load balancer", system: H, slug: "topology", tab: "topology", global: "haproxy.view" },
  { id: "haproxy-config", label: "Config", icon: "file", section: "Load balancer", system: H, slug: "config", tab: "config", global: "haproxy.configure" },

  { id: "alerts", label: "Alerts", icon: "bell", section: "Hub" },
  { id: "activity", label: "Activity", icon: "audit", section: "Hub", perm: "audit.view" },
  { id: "users", label: "Users", icon: "key", section: "Administration", slug: "admin/users", global: "users.manage" },
  { id: "roles", label: "Roles", icon: "lock", section: "Administration", slug: "admin/roles", global: "users.manage" },
  { id: "manage-clusters", label: "Clusters", icon: "logo", section: "Administration", slug: "admin/clusters", global: "clusters.manage" },
  { id: "system", label: "System settings", icon: "gear", section: "Administration", slug: "admin/system", global: "settings.manage" },
  { id: "settings", label: "My settings", icon: "gear", section: "You", locked: true },
];

export function pathOf(n: NavItem): string {
  const sys = systemById(n.system);
  return `${sys ? sys.base : ""}/${n.slug || n.id}`;
}
/** URL of a page by id, e.g. to("map") -> "/k8s/map". */
export function to(id: string): string {
  const n = NAV.find((x) => x.id === id);
  return n ? pathOf(n) : "/";
}
