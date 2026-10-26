<script setup lang="ts">
// Home: the health of every system at a glance, open alerts and recent activity.
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { hub } from "@/api/client";
import Badge from "@/components/Badge.vue";
import EmptyState from "@/components/EmptyState.vue";
import Icon from "@/components/Icon.vue";
import { fmt, type Level } from "@/lib/format";
import { backendHealth, hx, num, type HxOverview } from "@/lib/haproxy";
import { SYSTEMS, to } from "@/lib/nav";
import { fleet, fleetLevel, refreshFleet } from "@/stores/fleet";
import { useHaproxy } from "@/stores/haproxy";
import { useSession } from "@/stores/session";
import { rememberSystem } from "@/stores/ui";

const s = useSession(), h = useHaproxy(), router = useRouter();
const systems = computed(() => SYSTEMS.filter((x) => x.visible(s)));
const has = (id: string) => systems.value.some((x) => x.id === id);

// HAProxy: one overview per instance
const lbs = ref<{ inst: any; data?: HxOverview; error?: string }[] | null>(null);
async function loadLbs() {
  if (!has("haproxy")) return;
  try {
    const list = await hx.list();
    lbs.value = await Promise.all(list.map(async (inst) => {
      try { return { inst, data: await hx.overview(inst.id) }; } catch (e: any) { return { inst, error: e.message }; }
    }));
  } catch { lbs.value = []; }
}
function lbSummary(d: HxOverview) {
  const servers = d.backends.flatMap((b) => b.servers), up = servers.filter((x) => x.state === "up" || x.state === "nocheck").length;
  const allDown = (b: (typeof d.backends)[number]) => backendHealth(b).level === "critical";
  const bad = d.backends.filter(allDown).length;
  const down = d.backends.filter((b) => !allDown(b)).flatMap((b) => b.servers).filter((x) => x.state === "down").length;
  const level: Level = bad ? "critical" : down ? "warning" : "good";
  return { up, total: servers.length, problems: bad + down, level,
    rate: d.frontends.reduce((a, f) => a + (Number(f.stats.req_rate ?? f.stats.rate) || 0), 0), frontends: d.frontends.filter((f) => !f.stats_page).length };
}

const alerts = ref<any[] | null>(null), activity = ref<any[] | null>(null);
async function loadFeeds() {
  try { alerts.value = (await hub.get("/alerts/events", { state: "firing,pending", limit: 20 })).events; } catch { alerts.value = []; }
  // Changes, not sign-ins: those stay on the Activity page.
  try { activity.value = (await hub.get<any[]>("/audit", { limit: 60 })).filter((a) => !["login", "session-revoke"].includes(a.action)).slice(0, 8); }
  catch { activity.value = null; }
}

const k8s = computed(() => {
  const rows = s.clusters.map((c) => ({ c, f: fleet[c.id], level: fleetLevel(c.id) }));
  return { rows, healthy: rows.filter((r) => r.level === "good").length, problems: rows.reduce((a, r) => a + (r.f?.summary?.problems.length || 0), 0) };
});
const lbTotals = computed(() => {
  const all = (lbs.value || []).filter((x) => x.data).map((x) => lbSummary(x.data!));
  return { up: all.reduce((a, x) => a + x.up, 0), total: all.reduce((a, x) => a + x.total, 0), problems: all.reduce((a, x) => a + x.problems, 0) };
});
const firing = computed(() => (alerts.value || []).filter((a) => a.state === "firing"));

function openCluster(id: number) { s.setCluster(id); rememberSystem("kubernetes"); router.push(to("overview")); }
function openLb(id: number) { h.select(id); rememberSystem("haproxy"); router.push(to("haproxy-overview")); }
const levelText: Record<string, string> = { good: "Healthy", warning: "Degraded", critical: "Critical", "": "Checking…" };

let timer = 0;
onMounted(() => {
  refreshFleet(); loadLbs(); loadFeeds();
  timer = window.setInterval(() => { if (!document.hidden) { loadLbs(); loadFeeds(); } }, 30000);
});
onBeforeUnmount(() => clearInterval(timer));
</script>

<template>
  <div class="home-kpis">
    <div v-if="has('kubernetes')" class="card stat"><span class="lbl">Kubernetes clusters</span><b>{{ k8s.healthy }} <small class="dim">/ {{ k8s.rows.length }}</small></b><span class="hint">healthy · {{ k8s.problems }} open problems</span></div>
    <div v-if="has('haproxy')" class="card stat"><span class="lbl">HAProxy servers up</span><b>{{ lbTotals.up }} <small class="dim">/ {{ lbTotals.total }}</small></b><span class="hint">{{ lbs?.length ?? "–" }} instances · {{ lbTotals.problems }} problems</span></div>
    <div class="card stat"><span class="lbl">Firing alerts</span><b :style="{ color: firing.length ? 'var(--critical)' : undefined }">{{ alerts ? firing.length : "–" }}</b><span class="hint">{{ (alerts?.length || 0) - firing.length }} pending</span></div>
    <div class="card stat"><span class="lbl">Failed actions</span><b :style="{ color: activity?.some((a) => !a.ok) ? 'var(--critical)' : undefined }">{{ activity ? activity.filter((a) => !a.ok).length : "–" }}</b><span class="hint">in the last {{ activity?.length || 0 }} changes</span></div>
  </div>

  <div class="home-grid">
    <section v-if="has('kubernetes')" class="card flush sys-card">
      <div class="card-head"><h2><span class="sys-ic"><Icon name="logo" /></span>Kubernetes</h2><span class="hint">{{ fmt.plural(k8s.rows.length, "cluster") }}</span>
        <div class="right"><button class="btn sm" @click="rememberSystem('kubernetes'); router.push(to('clusters'))">All clusters</button></div></div>
      <EmptyState v-if="!k8s.rows.length" title="No clusters yet" sub="Install an agent and add the cluster in Administration → Clusters." icon="logo" />
      <ul v-else class="sys-rows">
        <li v-for="r in k8s.rows" :key="r.c.id" @click="openCluster(r.c.id)">
          <span class="hdot" :class="r.level" />
          <div style="min-width:0"><div class="main-line">{{ r.c.name }}</div>
            <div class="sub-line"><template v-if="r.f?.summary">{{ r.f.summary.nodes.ready }}/{{ r.f.summary.nodes.total }} nodes · {{ r.f.summary.pods.total }} pods · {{ r.f.summary.problems.length }} problems</template>
              <template v-else-if="r.f?.error">Unreachable</template><template v-else>Checking…</template></div></div>
          <div class="meta"><Badge :level="r.level || 'neutral'" :label="r.f?.error ? 'Unreachable' : levelText[r.level]" />
            <div v-if="r.f?.summary" style="margin-top:4px">CPU {{ fmt.pct(r.f.summary.cpu?.percent) }} · Mem {{ fmt.pct(r.f.summary.memory?.percent) }}</div></div>
        </li>
      </ul>
    </section>

    <section v-if="has('haproxy')" class="card flush sys-card">
      <div class="card-head"><h2><span class="sys-ic"><Icon name="lb" /></span>HAProxy</h2><span class="hint">{{ lbs ? fmt.plural(lbs.length, "instance") : "" }}</span></div>
      <div v-if="!lbs" class="card-body"><span class="hint">Loading…</span></div>
      <EmptyState v-else-if="!lbs.length" title="No HAProxy connected yet" sub="Open HAProxy in the system switcher to add one." icon="lb" />
      <ul v-else class="sys-rows">
        <li v-for="x in lbs" :key="x.inst.id" @click="openLb(x.inst.id)">
          <span class="hdot" :class="x.data ? lbSummary(x.data).level : 'critical'" />
          <div style="min-width:0"><div class="main-line">{{ x.inst.name }}</div>
            <div class="sub-line"><template v-if="x.data">{{ lbSummary(x.data).frontends }} frontends · {{ x.data.backends.length }} backends · {{ lbSummary(x.data).up }}/{{ lbSummary(x.data).total }} servers up</template>
              <template v-else>{{ x.error }}</template></div></div>
          <div class="meta"><Badge v-if="x.data" :level="lbSummary(x.data).level" :label="lbSummary(x.data).problems ? `${lbSummary(x.data).problems} problems` : 'Healthy'" />
            <Badge v-else level="critical" label="Unreachable" />
            <div v-if="x.data" style="margin-top:4px">{{ num(lbSummary(x.data).rate) }} req/s</div></div>
        </li>
      </ul>
    </section>

    <section class="card flush">
      <div class="card-head"><h2>Open alerts</h2><div class="right"><RouterLink class="link" :to="to('alerts')">All alerts</RouterLink></div></div>
      <EmptyState v-if="alerts && !alerts.length" title="No open alerts" sub="Everything your rules watch looks fine." icon="ok" good />
      <ul v-else class="sys-rows">
        <li v-for="a in (alerts || []).slice(0, 6)" :key="a.id" @click="router.push(to('alerts'))">
          <Badge :level="a.state === 'firing' ? (a.severity === 'critical' ? 'critical' : 'warning') : 'pending'" :label="a.state === 'firing' ? 'Firing' : 'Pending'" />
          <div style="min-width:0"><div class="main-line" style="font-weight:500">{{ a.title }}</div><div class="sub-line">{{ a.cluster }} · {{ a.rule }}</div></div>
          <div class="meta">{{ fmt.ago(a.first_seen) }}</div></li>
      </ul>
    </section>

    <section v-if="activity" class="card flush">
      <div class="card-head"><h2>Recent changes</h2><div class="right"><RouterLink class="link" :to="to('activity')">All activity</RouterLink></div></div>
      <EmptyState v-if="!activity.length" title="No activity yet" icon="audit" />
      <ul v-else class="sys-rows">
        <li v-for="a in activity" :key="a.id" @click="router.push(to('activity'))">
          <Badge :level="!a.ok ? 'critical' : a.dry_run ? 'neutral' : 'good'" :label="!a.ok ? 'Failed' : a.dry_run ? 'Dry run' : 'Done'" />
          <div style="min-width:0"><div class="main-line" style="font-weight:500"><code>{{ a.action }}</code> {{ a.target }}</div>
            <div class="sub-line">{{ a.username }}<template v-if="a.cluster"> · {{ a.cluster }}</template></div></div>
          <div class="meta">{{ fmt.ago(a.time) }}</div></li>
      </ul>
    </section>
  </div>
</template>
