<script setup lang="ts">
import { to } from "@/lib/nav";
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import Badge from "@/components/Badge.vue";
import EmptyState from "@/components/EmptyState.vue";
import Icon from "@/components/Icon.vue";
import LineChart from "@/components/LineChart.vue";
import Skeleton from "@/components/Skeleton.vue";
import { startAI } from "@/lib/actions";
import { fmt, HEALTH, podLevel, problemLevel } from "@/lib/format";
import { useCluster } from "@/stores/cluster";
import { prefs } from "@/stores/prefs";
import { useSession } from "@/stores/session";
import { openDetail } from "@/stores/ui";

const s = useSession(), c = useCluster();
const RANGES = { "5m": 5 * 60e3, "15m": 15 * 60e3, "1h": 60 * 60e3 } as const;
const sum = computed(() => c.data?.summary);
const m = ref<any>(null);
let timer = 0;
async function poll() { if (!c.paused && !document.hidden) try { m.value = await c.loadMetrics("cluster"); } catch { /* retry */ } }
onMounted(() => { poll(); timer = window.setInterval(poll, 5000); });
onBeforeUnmount(() => clearInterval(timer));
const last = computed(() => m.value?.points?.[m.value.points.length - 1]);
const cores = (v: number) => (v >= 10 ? `${v.toFixed(0)} cores` : v >= 1 ? `${v.toFixed(1)} cores` : v === 0 ? "0" : `${Math.round(v * 1000)}m`);
const gib = (v: number) => (v === 0 ? "0" : v >= 10 ? `${v.toFixed(0)} GiB` : v >= 1 ? `${v.toFixed(1)} GiB` : `${Math.round(v * 1024)} MiB`);
const problems = computed(() => {
  const order: Record<string, number> = { critical: 0, serious: 1, warning: 2 };
  return [...(sum.value?.problems || [])].sort((a, b) => order[problemLevel(a)] - order[problemLevel(b)]);
});
const groups = computed(() => {
  const pods = c.data?.pods || [];
  return [
    { label: "Running", color: "var(--good)", n: pods.filter((p: any) => podLevel(p) === "good").length },
    { label: "Pending", color: "var(--warning)", n: pods.filter((p: any) => podLevel(p) === "pending").length },
    { label: "Unhealthy", color: "var(--critical)", n: pods.filter((p: any) => ["critical", "warning"].includes(podLevel(p))).length },
    { label: "Completed / other", color: "var(--neutral)", n: pods.filter((p: any) => podLevel(p) === "neutral").length },
  ];
});
const warnings = computed(() => (c.data?.events || []).filter((e: any) => e.type === "Warning").slice(0, 6));
const liveLabel = computed(() => (c.paused ? "Paused" : c.liveError ? "Metrics unavailable" : `Live${m.value?.resolution_s ? ` · new point every ~${Math.round(m.value.resolution_s)}s` : ""}`));
</script>

<template>
  <div v-if="!sum" class="grid kpis"><div v-for="i in 6" :key="i" class="card kpi"><Skeleton :rows="3" /></div></div>
  <template v-else>
    <div class="grid kpis">
      <div class="card kpi"><div class="label"><Icon name="heart" />Cluster health</div><div class="value" style="font-size:22px;padding:3px 0"><Badge :level="HEALTH[sum.health].level" :label="HEALTH[sum.health].label" lg /></div>
        <div class="foot">{{ sum.problems.length ? fmt.plural(sum.problems.length, "open problem") : "No open problems" }}</div></div>
      <div class="card kpi"><div class="label"><Icon name="nodes" />Nodes</div><div class="value">{{ sum.nodes.ready }}<small> / {{ sum.nodes.total }}</small></div>
        <div class="foot">{{ sum.nodes.total - sum.nodes.ready ? `${sum.nodes.total - sum.nodes.ready} not ready` : "All nodes ready" }}</div></div>
      <div class="card kpi"><div class="label"><Icon name="pods" />Pods</div><div class="value">{{ sum.pods.total }}</div>
        <div class="foot">{{ sum.pods.by_phase.Running || 0 }} running · {{ sum.pods.by_phase.Succeeded || 0 }} completed · {{ sum.namespaces }} namespaces</div></div>
      <div class="card kpi"><div class="label"><Icon name="deploy" />Deployments</div><div class="value">{{ sum.deployments.ready }}<small> / {{ sum.deployments.total }}</small></div>
        <div class="foot">{{ sum.deployments.ready === sum.deployments.total ? "All available" : `${sum.deployments.total - sum.deployments.ready} degraded` }}</div></div>
      <div class="card kpi"><div class="label"><Icon name="cpu" />CPU</div><div class="value">{{ fmt.pct(sum.cpu.percent) }}</div>
        <div class="meter"><span :style="{ width: `${Math.min(sum.cpu.percent || 0, 100)}%` }" /></div><div class="foot">{{ fmt.cpu(sum.cpu.usage_m) }} of {{ fmt.cpu(sum.cpu.allocatable_m) }}</div></div>
      <div class="card kpi"><div class="label"><Icon name="mem" />Memory</div><div class="value">{{ fmt.pct(sum.memory.percent) }}</div>
        <div class="meter mem"><span :style="{ width: `${Math.min(sum.memory.percent || 0, 100)}%` }" /></div><div class="foot">{{ fmt.bytes(sum.memory.usage_bytes) }} of {{ fmt.bytes(sum.memory.allocatable_bytes) }}</div></div>
    </div>

    <div class="section-bar"><h2>Resource usage</h2><span class="live" :class="{ off: c.paused, err: c.liveError }"><i />{{ liveLabel }}</span><span style="flex:1" />
      <div class="segmented sm" role="group" aria-label="Time range"><button v-for="(_, k) in RANGES" :key="k" :aria-pressed="prefs.range === k" @click="prefs.range = k">{{ k }}</button></div></div>
    <div class="two-col">
      <section class="card"><div class="card-head"><h2>CPU</h2><span class="sub hide-sm">cluster-wide</span>
          <div v-if="last && m" class="chart-now"><b>{{ cores(last[1] / 1000) }}</b>of {{ cores(m.cpu_allocatable_m / 1000) }} · {{ fmt.pct((last[1] / m.cpu_allocatable_m) * 100) }}</div></div>
        <div class="card-body"><LineChart v-if="m" :series="[{ label: 'CPU', color: 'var(--series-1)', points: m.points.map((p: any) => [p[0], p[1] / 1000]) }]" :y-fmt="cores"
          :refs="[{ v: m.cpu_allocatable_m / 1000, label: 'capacity' }]" :range="RANGES[prefs.range]" area :empty-sub="m.error || 'A point arrives each time metrics-server measures (about every 15 seconds).'" aria-label="Cluster CPU" /><Skeleton v-else /></div></section>
      <section class="card"><div class="card-head"><h2>Memory</h2><span class="sub hide-sm">cluster-wide</span>
          <div v-if="last && m" class="chart-now"><b>{{ gib(last[2] / 2 ** 30) }}</b>of {{ gib(m.memory_allocatable_bytes / 2 ** 30) }} · {{ fmt.pct((last[2] / m.memory_allocatable_bytes) * 100) }}</div></div>
        <div class="card-body"><LineChart v-if="m" :series="[{ label: 'Memory', color: 'var(--series-2)', points: m.points.map((p: any) => [p[0], p[2] / 2 ** 30]) }]" :y-fmt="gib"
          :refs="[{ v: m.memory_allocatable_bytes / 2 ** 30, label: 'capacity' }]" :range="RANGES[prefs.range]" area :empty-sub="m.error || 'Waiting for the first measurement.'" aria-label="Cluster memory" /><Skeleton v-else /></div></section>
    </div>

    <div class="grid one-two">
      <div class="stack-col">
        <section class="card flush"><div class="card-head"><h2>Problems</h2><span class="sub">{{ problems.length ? fmt.plural(problems.length, "issue") : "" }}</span>
            <div v-if="s.can('ai.use')" class="right"><button class="link" @click="startAI({ scope: 'cluster' })">Ask AI</button></div></div>
          <div class="card-body">
            <EmptyState v-if="!problems.length" title="All clear" sub="No unhealthy nodes, pods or workloads." icon="ok" good />
            <ul v-else class="list"><li v-for="(p, i) in problems.slice(0, 8)" :key="i" class="clickable" tabindex="0" @click="openDetail(p.kind, p.namespace, p.name)" @keydown.enter="openDetail(p.kind, p.namespace, p.name)">
              <span class="ic" :class="problemLevel(p) === 'critical' ? 'critical' : 'warning'"><Icon :name="problemLevel(p) === 'critical' ? 'crit' : 'warn'" /></span>
              <div><div class="main-line">{{ p.name }}</div><div class="sub-line">{{ p.kind }}<template v-if="p.namespace"> · {{ p.namespace }}</template> · {{ p.reason }}</div></div></li></ul>
          </div></section>
        <section class="card"><div class="card-head"><h2>Pod status</h2><span class="sub">{{ fmt.plural(c.data!.pods.length, "pod") }}</span><div class="right"><RouterLink class="link" to="/pods">View pods</RouterLink></div></div>
          <div class="card-body">
            <div class="stack" role="img"><span v-for="g in groups.filter((x) => x.n)" :key="g.label" :style="{ flex: g.n, background: g.color }" :title="`${g.label}: ${g.n}`" /></div>
            <div class="stack-legend"><div v-for="g in groups" :key="g.label"><span class="dot" :style="{ background: g.color }" />{{ g.label }}<b>{{ g.n }}</b></div></div>
          </div></section>
      </div>
      <section class="card flush"><div class="card-head"><h2>Recent warnings</h2><div class="right"><RouterLink class="link" :to="to('events')">All events</RouterLink></div></div>
        <div class="card-body">
          <EmptyState v-if="!warnings.length" title="No warnings" sub="No warning events in the cluster's event history." icon="ok" good />
          <ul v-else class="list"><li v-for="(e, i) in warnings" :key="i"><span class="ic warning"><Icon name="warn" /></span>
            <div style="min-width:0"><div class="main-line">{{ e.reason }} <span class="dim" style="font-weight:400">· {{ e.object }}</span></div><div class="sub-line">{{ e.message }}</div></div>
            <span class="meta">{{ fmt.age(e.age_s) }} ago</span></li></ul>
        </div></section>
    </div>
  </template>
</template>
