<script setup lang="ts">
import Logo from "./Logo.vue";
import { hub } from "@/api/client";
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { actApply } from "@/lib/actions";
import { HEALTH } from "@/lib/format";
import { backendHealth } from "@/lib/haproxy";
import { NAV, pathOf, SYSTEMS, systemById, to } from "@/lib/nav";
import { useHaproxy } from "@/stores/haproxy";
import { fleet, fleetLevel, refreshFleet } from "@/stores/fleet";
import { applyTheme, isDark, prefs } from "@/stores/prefs";
import { useCluster } from "@/stores/cluster";
import { useSession } from "@/stores/session";
import { closePanel, lastSystem, openMenu, rememberSystem, shell, type MenuItem } from "@/stores/ui";
// The terminal (xterm.js) is only downloaded when someone opens a shell.
const PodShell = defineAsyncComponent(() => import("./PodShell.vue"));
import ActionDialog from "./ActionDialog.vue";
import Badge from "./Badge.vue";
import Icon from "./Icon.vue";
import MenuPopover from "./MenuPopover.vue";
import PanelHost from "@/panels/PanelHost.vue";
import Toasts from "./Toasts.vue";

const s = useSession(), c = useCluster(), h = useHaproxy(), route = useRoute(), router = useRouter();
const now = ref(Date.now());
const navItem = computed(() => route.meta.nav as (typeof NAV)[number] | undefined);

// The system in the sidebar: the current page's system, or the last one used (on shared pages).
const systems = computed(() => SYSTEMS.filter((x) => x.visible(s)));
const system = computed(() => systemById(navItem.value?.system) || systems.value.find((x) => x.id === lastSystem.id) || systems.value[0]);
const allowed = (n: (typeof NAV)[number]) => {
  if (n.hidden || (!n.locked && prefs.hidden.includes(n.id))) return false;
  if (n.global) return s.canGlobal(n.global);
  if (n.cluster && !s.current) return false;
  if (n.perm) return s.can(n.perm);
  return true;
};
const group = (items: typeof NAV) => {
  const out: { name: string; items: typeof NAV }[] = [];
  for (const n of items) {
    let g = out.find((x) => x.name === n.section);
    if (!g) out.push((g = { name: n.section, items: [] }));
    g.items.push(n);
  }
  return out;
};
const sections = computed(() => group(NAV.filter((n) => n.system && n.system === system.value?.id && allowed(n))));
const shared = computed(() => group(NAV.filter((n) => !n.system && allowed(n))));
const isActive = (n: (typeof NAV)[number]) => route.path === pathOf(n);
type Counts = Record<string, { n: number | string; alert: boolean }>;
const haproxyCounts = computed<Counts>(() => {
  const d = h.data;
  if (!d) return {} as Counts;
  // Same counting as the Overview's problem list: a backend with no server up is one problem,
  // otherwise each down server is one.
  const allDown = (b: (typeof d.backends)[number]) => backendHealth(b).level === "critical";
  const empty = d.backends.filter(allDown).length;
  const down = d.backends.filter((b) => !allDown(b)).flatMap((b) => b.servers).filter((x) => x.state === "down").length;
  return {
    "haproxy-overview": { n: down + empty || "", alert: down + empty > 0 },
    "haproxy-services": { n: d.frontends.filter((f) => !f.stats_page).length, alert: false },
    "haproxy-backends": { n: d.backends.length, alert: down > 0 },
  };
});
const counts = computed<Record<string, { n: number | string; alert: boolean }>>(() => {
  const d = c.data;
  if (!d) return { ...haproxyCounts.value };
  return {
    overview: { n: d.summary.problems.length || "", alert: d.summary.problems.length > 0 },
    nodes: { n: d.nodes.length, alert: d.nodes.some((x: any) => !x.ready) },
    deployments: { n: d.deployments.length, alert: d.deployments.some((x: any) => x.ready < x.desired) },
    statefulsets: { n: d.statefulsets.length || "", alert: d.statefulsets.some((x: any) => x.ready < x.desired) },
    daemonsets: { n: d.daemonsets.length || "", alert: d.daemonsets.some((x: any) => x.ready < x.desired) },
    jobs: { n: d.jobs.length || "", alert: d.jobs.some((x: any) => x.status === "Failed") },
    pods: { n: d.pods.length, alert: d.pods.some((x: any) => x.problem) },
    services: { n: d.services.length, alert: false },
    events: { n: d.events.filter((e: any) => e.type === "Warning").length || "", alert: false },
    clusters: { n: s.clusters.length > 1 ? s.clusters.length : "", alert: s.clusters.some((x) => fleetLevel(x.id) === "critical") },
    ...haproxyCounts.value,
  };
});
const onHaproxy = computed(() => navItem.value?.system === "haproxy");
const live = computed(() => !!navItem.value?.cluster || onHaproxy.value);   // pages with auto-refresh
const health = computed(() => {
  if (navItem.value?.cluster && c.data) return HEALTH[c.data.summary.health];
  if (onHaproxy.value && h.data) {
    const lv = h.data.backends.map((b) => backendHealth(b).level);
    return lv.includes("critical") ? { level: "critical", label: "Backend down" } : lv.includes("warning") ? { level: "warning", label: "Degraded" } : { level: "good", label: "Healthy" };
  }
  return null;
});
const updated = computed(() => {
  const [paused, at] = onHaproxy.value ? [h.paused, h.updated] : [c.paused, c.lastUpdated];
  if (paused) return "Paused";
  if (!at) return "";
  const sec = Math.round((now.value - at) / 1000);
  return sec < 5 ? "Updated just now" : `Updated ${sec}s ago`;
});
const initials = computed(() => (s.me?.user.display_name || s.me?.user.username || "?").split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase());

// Tell open tabs when a newer dashboard has been deployed (single-page apps keep running old code otherwise).
const loadedBuild = ref(""), newBuild = ref(false);
async function checkBuild() {
  try {
    const b = (await hub.get<{ build: string }>("/health")).build;
    if (!loadedBuild.value) loadedBuild.value = b; else if (b !== loadedBuild.value) newBuild.value = true;
  } catch { /* hub restarting */ }
}
const reloadPage = () => window.location.reload();
let timer = 0, tick = 0, fleetTimer = 0, buildTimer = 0;
function schedule() {
  clearInterval(timer);
  timer = window.setInterval(() => { if (!c.paused && !document.hidden) c.refresh(); }, (prefs.refresh || 10) * 1000);
}
watch(() => prefs.refresh, schedule);
watch(() => s.current?.id, () => { c.reset(); closePanel(); c.refresh(); }, { immediate: false });
onMounted(() => {
  if (s.current) c.refresh();
  schedule();
  refreshFleet();
  fleetTimer = window.setInterval(() => { if (!document.hidden) refreshFleet(); }, 30000);
  tick = window.setInterval(() => { now.value = Date.now(); }, 1000);
  checkBuild();
  buildTimer = window.setInterval(() => { if (!document.hidden) checkBuild(); }, 120000);
  document.addEventListener("keydown", onKey);
});
onBeforeUnmount(() => { clearInterval(timer); clearInterval(tick); clearInterval(fleetTimer); clearInterval(buildTimer); document.removeEventListener("keydown", onKey); });

function onKey(e: KeyboardEvent) {
  const typing = /INPUT|SELECT|TEXTAREA/.test((document.activeElement as HTMLElement)?.tagName || "");
  if (typing || e.metaKey || e.ctrlKey || e.altKey || document.querySelector("dialog[open]")) return;
  if (e.key === "/") { const inp = document.querySelector<HTMLInputElement>(".search input"); if (inp) { e.preventDefault(); inp.focus(); inp.select(); } }
  else if (e.key === "r") refreshNow();
}

function systemMenu(e: MouseEvent) {
  const el = e.currentTarget as HTMLElement;
  el.dataset.menuAlign = "left";
  openMenu(el, [{ label: "Home · all systems", icon: "overview", run: () => router.push(to("home")) }, { sep: true },
    ...systems.value.map((x) => ({ label: x.label, icon: x.id === system.value?.id ? "ok" : x.icon,
      run: () => { rememberSystem(x.id); router.push(x.id === "kubernetes" ? to(s.current ? "overview" : "clusters") : `${x.base}/overview`); } }))]);
}
function instanceMenu(e: MouseEvent) {
  const el = e.currentTarget as HTMLElement;
  el.dataset.menuAlign = "left";
  const items: MenuItem[] = [{ note: "Switch HAProxy" }, ...(h.instances || []).map((i) => ({ label: i.name, icon: i.id === h.currentId ? "ok" : "lb",
    run: () => { h.select(i.id); if (!onHaproxy.value) router.push(to("haproxy-overview")); } }))];
  if (!h.instances?.length) items.push({ note: "No HAProxy connected yet" });
  if (s.canGlobal("clusters.manage")) items.push({ sep: true }, { label: "Add HAProxy…", icon: "gear", run: () => router.push(to("haproxy-overview")) });
  openMenu(el, items);
}
const instanceLevel = computed(() => (health.value && onHaproxy.value ? health.value.level : h.data ? "good" : ""));
watch(() => system.value?.id, (id) => { if (id === "haproxy" && h.instances == null) h.loadInstances(); }, { immediate: true });
function refreshNow() { if (onHaproxy.value) h.refresh(); else c.refresh(); }
function togglePauseAny() {
  if (onHaproxy.value) { h.paused = !h.paused; if (!h.paused) h.refresh(); }
  else togglePause();
}

function clusterMenu(e: MouseEvent) {
  const el = e.currentTarget as HTMLElement;
  el.dataset.menuAlign = "left";
  const items: MenuItem[] = [{ note: "Switch cluster" }, ...s.clusters.map((x) => ({
    label: `${x.name}${fleet[x.id]?.error ? " · unreachable" : fleet[x.id]?.summary ? ` · ${fleet[x.id].summary.health}` : ""}`,
    icon: x.id === s.current?.id ? "ok" : "logo",
    run: () => { s.setCluster(x.id); if (!navItem.value?.cluster) router.push(to("overview")); },
  })), { sep: true }, { label: "All clusters", icon: "globe", run: () => router.push(to("clusters")) }];
  if (s.canGlobal("clusters.manage")) items.push({ label: "Manage clusters…", icon: "gear", run: () => router.push(to("manage-clusters")) });
  openMenu(el, items);
}
function userMenu(e: MouseEvent) {
  openMenu(e.currentTarget as HTMLElement, [
    { note: `Signed in as ${s.me?.user.username}` },
    { label: "My settings", icon: "gear", run: () => router.push(to("settings")) },
    { label: "Change password", icon: "key", run: () => router.push("/password") },
    { sep: true },
    { label: "Sign out", icon: "x", run: async () => { await s.logout(); router.replace("/login"); } },
  ]);
}
function togglePause() { c.paused = !c.paused; if (!c.paused) c.refresh(); }
function toggleTheme() { prefs.theme = isDark() ? "light" : "dark"; applyTheme(); }
const canApply = computed(() => navItem.value?.cluster && s.can("apply.yaml") && c.control);
</script>

<template>
  <div class="app">
    <aside class="sidebar">
      <div class="brand"><Logo :size="28" /><span>k10y<span class="brand-sub">-agent</span></span></div>
      <button v-if="system" class="system-switch" title="Switch system" aria-haspopup="menu" @click="systemMenu">
        <span class="sys-ic"><Icon :name="navItem?.id === 'home' ? 'overview' : system.icon" /></span>
        <span class="cs-text"><span class="cs-label">System</span><b>{{ navItem?.id === "home" ? "Home" : system.label }}</b></span>
        <Icon name="sort" />
      </button>
      <button v-if="system?.id === 'kubernetes' && navItem?.id !== 'home'" class="cluster-switch" title="Switch cluster" aria-haspopup="menu" @click="clusterMenu">
        <span class="hdot" :class="s.current ? fleetLevel(s.current.id) : ''" />
        <span class="cs-text"><span class="cs-label">Cluster</span><b>{{ s.current?.name || "No cluster" }}</b></span>
        <Icon name="sort" />
      </button>
      <button v-else-if="system?.id === 'haproxy' && navItem?.id !== 'home'" class="cluster-switch" title="Switch HAProxy" aria-haspopup="menu" @click="instanceMenu">
        <span class="hdot" :class="instanceLevel" />
        <span class="cs-text"><span class="cs-label">HAProxy</span><b>{{ h.current?.name || (h.instances?.length === 0 ? "None yet" : "…") }}</b></span>
        <Icon name="sort" />
      </button>
      <nav class="nav" aria-label="Main">
        <RouterLink :to="to('home')" :aria-current="navItem?.id === 'home' ? 'page' : undefined"><Icon name="overview" /><span>Home</span></RouterLink>
        <template v-if="navItem?.id !== 'home'">
          <template v-for="g in sections" :key="g.name">
            <div class="nav-label">{{ g.name }}</div>
            <RouterLink v-for="n in g.items" :key="n.id" :to="pathOf(n)" :aria-current="isActive(n) ? 'page' : undefined">
              <Icon :name="n.icon" /><span>{{ n.label }}</span>
              <span v-if="counts[n.id] && counts[n.id].n !== ''" class="count" :class="{ alert: counts[n.id].alert }">{{ counts[n.id].n }}</span>
            </RouterLink>
          </template>
        </template>
        <div v-else class="nav-systems">
          <div class="nav-label">Systems</div>
          <RouterLink v-for="x in systems" :key="x.id" :to="x.id === 'kubernetes' ? to(s.current ? 'overview' : 'clusters') : `${x.base}/overview`" @click="rememberSystem(x.id)">
            <Icon :name="x.icon" /><span>{{ x.label }}</span></RouterLink>
        </div>
        <div class="nav-shared">
          <template v-for="g in shared" :key="g.name">
            <div class="nav-label">{{ g.name }}</div>
            <RouterLink v-for="n in g.items" :key="n.id" :to="pathOf(n)" :aria-current="isActive(n) ? 'page' : undefined">
              <Icon :name="n.icon" /><span>{{ n.label }}</span></RouterLink>
          </template>
        </div>
      </nav>
      <div class="sidebar-foot">
        <template v-if="system?.id === 'kubernetes' && c.info && navItem?.id !== 'home'"><strong>{{ c.info.cluster }}</strong><br>Agent v{{ c.info.agent_version }}<br>{{ c.info.kubernetes_version }}</template>
        <template v-else-if="onHaproxy && h.data"><strong>{{ h.data.instance.name }}</strong><br>config version {{ h.data.config_version }}</template>
        <template v-else>k10y hub v{{ s.me?.version }}</template>
      </div>
    </aside>

    <div class="main">
      <header class="topbar">
        <div>
          <div class="crumb">
            <template v-if="navItem?.cluster && s.current"><Icon name="logo" />{{ s.current.name }}<span v-if="c.info" class="dim">·</span>{{ c.info?.kubernetes_version }}</template>
            <template v-else-if="onHaproxy && h.current"><Icon name="lb" />HAProxy<span class="dim">·</span>{{ h.current.name }}</template>
            <template v-else-if="system && navItem?.system">{{ system.label }}</template>
            <template v-else>k10y</template>
          </div>
          <h1>{{ navItem?.label }}</h1>
        </div>
        <Badge v-if="health" :level="health.level" :label="health.label" lg />
        <div class="spacer" />
        <span v-if="live" class="updated" aria-live="polite">{{ updated }}</span>
        <button v-if="live" class="btn icon-only ghost" :aria-pressed="onHaproxy ? h.paused : c.paused" :title="(onHaproxy ? h.paused : c.paused) ? 'Resume auto-refresh' : 'Pause auto-refresh'"
                @click="togglePauseAny"><Icon :name="(onHaproxy ? h.paused : c.paused) ? 'play' : 'pause'" /></button>
        <button v-if="canApply" class="btn" @click="actApply()"><Icon name="upload" /><span>Apply YAML</span></button>
        <button v-if="live" class="btn" :class="{ spin: onHaproxy ? h.loading : c.loading }" title="Refresh now (R)" @click="refreshNow"><Icon name="refresh" /><span>Refresh</span></button>
        <button class="btn icon-only ghost" title="Toggle theme" @click="toggleTheme"><Icon :name="isDark() ? 'sun' : 'moon'" /></button>
        <button class="btn ghost user-btn" aria-haspopup="menu" title="Account" @click="userMenu"><span class="avatar">{{ initials }}</span></button>
      </header>
      <div class="content">
        <div v-if="newBuild" class="banner show info" role="status"><Icon name="refresh" /><span class="msg">A new version of k10y is available.</span>
          <button class="btn primary" @click="reloadPage">Reload</button></div>
        <div v-if="navItem?.cluster && c.error" class="banner show" role="alert"><Icon name="crit" /><span class="msg">{{ c.error }}</span>
          <button class="btn" @click="c.refresh()">Retry</button></div>
        <slot />
      </div>
    </div>
  </div>
  <PanelHost />
  <ActionDialog />
  <PodShell v-if="shell.current" />
  <MenuPopover />
  <Toasts />
</template>
