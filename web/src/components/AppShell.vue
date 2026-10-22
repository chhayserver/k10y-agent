<script setup lang="ts">
import Logo from "./Logo.vue";
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { actApply } from "@/lib/actions";
import { HEALTH } from "@/lib/format";
import { NAV } from "@/lib/nav";
import { fleet, fleetLevel, refreshFleet } from "@/stores/fleet";
import { applyTheme, isDark, prefs } from "@/stores/prefs";
import { useCluster } from "@/stores/cluster";
import { useSession } from "@/stores/session";
import { closePanel, openMenu, type MenuItem } from "@/stores/ui";
import ActionDialog from "./ActionDialog.vue";
import PodShell from "./PodShell.vue";
import Badge from "./Badge.vue";
import Icon from "./Icon.vue";
import MenuPopover from "./MenuPopover.vue";
import PanelHost from "@/panels/PanelHost.vue";
import Toasts from "./Toasts.vue";

const s = useSession(), c = useCluster(), route = useRoute(), router = useRouter();
const now = ref(Date.now());
const navItem = computed(() => route.meta.nav as (typeof NAV)[number] | undefined);

const visible = computed(() => NAV.filter((n) => {
  if (!n.locked && prefs.hidden.includes(n.id)) return false;
  if (n.global) return s.canGlobal(n.global);
  if (n.cluster && !s.current) return false;
  if (n.perm) return s.can(n.perm);
  return true;
}));
const sections = computed(() => {
  const out: { name: string; items: typeof NAV }[] = [];
  for (const n of visible.value) {
    let g = out.find((x) => x.name === n.section);
    if (!g) out.push((g = { name: n.section, items: [] }));
    g.items.push(n);
  }
  return out;
});
const counts = computed<Record<string, { n: number | string; alert: boolean }>>(() => {
  const d = c.data;
  if (!d) return {} as Record<string, { n: number | string; alert: boolean }>;
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
  };
});
const health = computed(() => (navItem.value?.cluster && c.data ? HEALTH[c.data.summary.health] : null));
const updated = computed(() => {
  if (c.paused) return "Paused";
  if (!c.lastUpdated) return "";
  const sec = Math.round((now.value - c.lastUpdated) / 1000);
  return sec < 5 ? "Updated just now" : `Updated ${sec}s ago`;
});
const initials = computed(() => (s.me?.user.display_name || s.me?.user.username || "?").split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase());

let timer = 0, tick = 0, fleetTimer = 0;
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
  document.addEventListener("keydown", onKey);
});
onBeforeUnmount(() => { clearInterval(timer); clearInterval(tick); clearInterval(fleetTimer); document.removeEventListener("keydown", onKey); });

function onKey(e: KeyboardEvent) {
  const typing = /INPUT|SELECT|TEXTAREA/.test((document.activeElement as HTMLElement)?.tagName || "");
  if (typing || e.metaKey || e.ctrlKey || e.altKey || document.querySelector("dialog[open]")) return;
  if (e.key === "/") { const inp = document.querySelector<HTMLInputElement>(".search input"); if (inp) { e.preventDefault(); inp.focus(); inp.select(); } }
  else if (e.key === "r") c.refresh();
}

function clusterMenu(e: MouseEvent) {
  const el = e.currentTarget as HTMLElement;
  el.dataset.menuAlign = "left";
  const items: MenuItem[] = [{ note: "Switch cluster" }, ...s.clusters.map((x) => ({
    label: `${x.name}${fleet[x.id]?.error ? " · unreachable" : fleet[x.id]?.summary ? ` · ${fleet[x.id].summary.health}` : ""}`,
    icon: x.id === s.current?.id ? "ok" : "logo",
    run: () => { s.setCluster(x.id); if (!navItem.value?.cluster) router.push("/overview"); },
  })), { sep: true }, { label: "All clusters", icon: "globe", run: () => router.push("/clusters") }];
  if (s.canGlobal("clusters.manage")) items.push({ label: "Manage clusters…", icon: "gear", run: () => router.push("/manage-clusters") });
  openMenu(el, items);
}
function userMenu(e: MouseEvent) {
  openMenu(e.currentTarget as HTMLElement, [
    { note: `Signed in as ${s.me?.user.username}` },
    { label: "My settings", icon: "gear", run: () => router.push("/settings") },
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
      <button class="cluster-switch" title="Switch cluster" aria-haspopup="menu" @click="clusterMenu">
        <span class="hdot" :class="s.current ? fleetLevel(s.current.id) : ''" />
        <span class="cs-text"><span class="cs-label">Cluster</span><b>{{ s.current?.name || "No cluster" }}</b></span>
        <Icon name="sort" />
      </button>
      <nav class="nav" aria-label="Main">
        <template v-for="g in sections" :key="g.name">
          <div class="nav-label">{{ g.name }}</div>
          <RouterLink v-for="n in g.items" :key="n.id" :to="`/${n.id}`" :aria-current="route.path === `/${n.id}` ? 'page' : undefined">
            <Icon :name="n.icon" /><span>{{ n.label }}</span>
            <span v-if="counts[n.id] && counts[n.id].n !== ''" class="count" :class="{ alert: counts[n.id].alert }">{{ counts[n.id].n }}</span>
          </RouterLink>
        </template>
      </nav>
      <div class="sidebar-foot">
        <template v-if="c.info"><strong>{{ c.info.cluster }}</strong><br>Agent v{{ c.info.agent_version }}<br>{{ c.info.kubernetes_version }}</template>
        <template v-else>k10y hub v{{ s.me?.version }}</template>
      </div>
    </aside>

    <div class="main">
      <header class="topbar">
        <div>
          <div class="crumb">
            <template v-if="navItem?.cluster && s.current"><Icon name="logo" />{{ s.current.name }}<span v-if="c.info" class="dim">·</span>{{ c.info?.kubernetes_version }}</template>
            <template v-else>k10y</template>
          </div>
          <h1>{{ navItem?.label }}</h1>
        </div>
        <Badge v-if="health" :level="health.level" :label="health.label" lg />
        <div class="spacer" />
        <span v-if="navItem?.cluster" class="updated" aria-live="polite">{{ updated }}</span>
        <button v-if="navItem?.cluster" class="btn icon-only ghost" :aria-pressed="c.paused" :title="c.paused ? 'Resume auto-refresh' : 'Pause auto-refresh'"
                @click="togglePause"><Icon :name="c.paused ? 'play' : 'pause'" /></button>
        <button v-if="canApply" class="btn" @click="actApply()"><Icon name="upload" /><span>Apply YAML</span></button>
        <button v-if="navItem?.cluster" class="btn" :class="{ spin: c.loading }" title="Refresh now (R)" @click="c.refresh()"><Icon name="refresh" /><span>Refresh</span></button>
        <button class="btn icon-only ghost" title="Toggle theme" @click="toggleTheme"><Icon :name="isDark() ? 'sun' : 'moon'" /></button>
        <button class="btn ghost user-btn" aria-haspopup="menu" title="Account" @click="userMenu"><span class="avatar">{{ initials }}</span></button>
      </header>
      <div class="content">
        <div v-if="navItem?.cluster && c.error" class="banner show" role="alert"><Icon name="crit" /><span class="msg">{{ c.error }}</span>
          <button class="btn" @click="c.refresh()">Retry</button></div>
        <slot />
      </div>
    </div>
  </div>
  <PanelHost />
  <ActionDialog />
  <PodShell />
  <MenuPopover />
  <Toasts />
</template>
