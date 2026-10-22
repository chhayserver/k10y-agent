<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import Badge from "@/components/Badge.vue";
import DataTable from "@/components/DataTable.vue";
import LineChart from "@/components/LineChart.vue";
import Meter from "@/components/Meter.vue";
import RowMenu from "@/components/RowMenu.vue";
import Skeleton from "@/components/Skeleton.vue";
import { menuFor } from "@/lib/actions";
import { fmt } from "@/lib/format";
import { useCluster } from "@/stores/cluster";
import { page, prefs } from "@/stores/prefs";
import { openDetail } from "@/stores/ui";

const c = useCluster(), state = page("nodes", { key: "name", dir: 1 });
const RANGES = { "5m": 5 * 60e3, "15m": 15 * 60e3, "1h": 60 * 60e3 } as const;
const m = ref<any>(null);
let timer = 0;
async function poll() { if (!c.paused && !document.hidden) try { m.value = await c.loadMetrics("nodes"); } catch { /* retry */ } }
onMounted(() => { poll(); timer = window.setInterval(poll, 5000); });
onBeforeUnmount(() => clearInterval(timer));
const names = computed(() => Object.keys(m.value?.nodes || {}).sort().slice(0, 8));
const series = (k: 1 | 2, cap: string) => names.value.map((n, i) => ({ label: n, color: `var(--series-${i + 1})`,
  points: m.value.nodes[n].points.map((p: any) => [p[0], m.value.nodes[n][cap] ? (p[k] / m.value.nodes[n][cap]) * 100 : 0]) }));
const pct = (v: number) => `${Math.round(v)}%`;
const columns = [
  { key: "name", label: "Name", cls: "primary" }, { key: "ready", label: "Status", sort: (n: any) => (n.ready ? 1 : 0) },
  { key: "roles", label: "Roles", cls: "wrap-sm", sort: (n: any) => n.roles.join() }, { key: "cpu_percent", label: "CPU" },
  { key: "memory_percent", label: "Memory" }, { key: "cap", label: "Capacity", sort: (n: any) => n.cpu_allocatable_m },
  { key: "internal_ip", label: "Internal IP", cls: "mono" }, { key: "kubelet_version", label: "Version" },
  { key: "age_s", label: "Age", num: true }, { key: "_a", label: "Actions", nosort: true, cls: "actions" },
];
</script>
<template>
  <div class="section-bar"><h2>Usage by node</h2>
    <span class="live" :class="{ off: c.paused, err: c.liveError }"><i />{{ c.paused ? "Paused" : c.liveError ? "Metrics unavailable" : "Live" }}</span>
    <span class="legend"><span v-for="(n, i) in names.length > 1 ? names : []" :key="n"><i class="swatch" :style="{ background: `var(--series-${i + 1})` }" />{{ n }}</span></span>
    <span style="flex:1" /><div class="segmented sm" role="group"><button v-for="(_, k) in RANGES" :key="k" :aria-pressed="prefs.range === k" @click="prefs.range = k">{{ k }}</button></div>
  </div>
  <div class="two-col">
    <section class="card"><div class="card-head"><h2>CPU</h2><span class="sub">% of node allocatable</span></div>
      <div class="card-body"><LineChart v-if="m" :series="series(1, 'cpu_allocatable_m')" :y-fmt="pct" :refs="[{ v: 100, label: '100%' }]" :range="RANGES[prefs.range]" aria-label="CPU by node" /><Skeleton v-else /></div></section>
    <section class="card"><div class="card-head"><h2>Memory</h2><span class="sub">% of node allocatable</span></div>
      <div class="card-body"><LineChart v-if="m" :series="series(2, 'memory_allocatable_bytes')" :y-fmt="pct" :refs="[{ v: 100, label: '100%' }]" :range="RANGES[prefs.range]" aria-label="Memory by node" /><Skeleton v-else /></div></section>
  </div>
  <section class="card flush"><div class="card-head"><h2>Nodes</h2><span v-if="c.data" class="sub">{{ c.data.nodes.filter((n: any) => n.ready).length }} of {{ fmt.plural(c.data.nodes.length, "node") }} ready</span></div>
    <div class="card-body table-wrap">
      <Skeleton v-if="!c.data" />
      <DataTable v-else v-model:sort="state.sort" :rows="c.data.nodes" :columns="columns" clickable empty-title="No nodes" @row="(n) => openDetail('Node', '', n.name)">
        <template #ready="{ row }"><Badge :level="row.ready ? 'good' : 'critical'" :label="row.ready ? 'Ready' : 'NotReady'" /> <Badge v-if="row.unschedulable" level="neutral" label="Cordoned" /> <Badge v-for="p in row.pressure" :key="p" level="serious" :label="p" /></template>
        <template #roles="{ row }">{{ row.roles.join(", ") || "worker" }}</template>
        <template #cpu_percent="{ row }"><Meter :pct="row.cpu_percent" /></template>
        <template #memory_percent="{ row }"><Meter :pct="row.memory_percent" mem /></template>
        <template #cap="{ row }">{{ fmt.cpu(row.cpu_allocatable_m) }} · {{ fmt.bytes(row.memory_allocatable_bytes) }}</template>
        <template #age_s="{ row }">{{ fmt.age(row.age_s) }}</template>
        <template #_a="{ row }"><RowMenu :items="() => menuFor('node', row)" :label="row.name" /></template>
      </DataTable>
    </div></section>
</template>
