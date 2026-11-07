<script setup lang="ts">
// Right-sizing: what each workload really uses (95th percentile and peak over the last days, per pod) against
// its requests and limits, with recommended values and what you'd get back.
import { computed, onMounted, ref, watch } from "vue";
import { hub } from "@/api/client";
import Badge from "@/components/Badge.vue";
import DataTable from "@/components/DataTable.vue";
import EmptyState from "@/components/EmptyState.vue";
import Icon from "@/components/Icon.vue";
import Skeleton from "@/components/Skeleton.vue";
import Toolbar from "@/components/Toolbar.vue";
import { useFiltered } from "@/lib/list";
import { page } from "@/stores/prefs";
import { useSession } from "@/stores/session";
import { openDetail } from "@/stores/ui";

const s = useSession(), state = page("rightsizing", { key: "level", dir: 1 });
const data = ref<{ workloads: any[]; hours: number } | null>(null), error = ref(""), days = ref(7);
async function load() {
  data.value = null;
  try { data.value = await hub.get(`/clusters/${s.current!.id}/rightsizing`, { days: days.value }); error.value = ""; } catch (e: any) { error.value = e.message; }
}
onMounted(load);
watch([() => s.current?.id, days], load);
const ORDER: Record<string, number> = { critical: 0, warning: 1, info: 2, good: 3 };
const LABEL: Record<string, string> = { critical: "Act now", warning: "Fix", info: "Can save", good: "Fits" };
const BADGE: Record<string, string> = { critical: "critical", warning: "warning", info: "neutral", good: "good" };
const { rows, all, count } = useFiltered(() => data.value?.workloads, state, (x: any) => `${x.workload} ${x.namespace} ${x.findings.join(" ")}`,
  (x, f) => f === "all" || (f === "fix" ? x.level === "critical" || x.level === "warning" : f === "save" ? x.savings.cpu_m > 0 || x.savings.memory_bytes > 0 : true));
const columns = [{ key: "level", label: "Verdict", sort: (x: any) => ORDER[x.level] }, { key: "workload", label: "Workload", cls: "primary" },
  { key: "namespace", label: "Namespace" }, { key: "pods", label: "Pods", num: true },
  { key: "cpu", label: "CPU per pod: request → use (p95 / peak)", sort: (x: any) => x.cpu.request - x.cpu.p95 },
  { key: "memory", label: "Memory per pod: request · limit → peak", sort: (x: any) => x.memory.request - x.memory.peak },
  { key: "findings", label: "What to do", cls: "wrap", nosort: true },
  { key: "savings", label: "Frees", num: true, sort: (x: any) => x.savings.cpu_m }];
const cpu = (m: number) => (!m ? "–" : m >= 1000 ? `${(m / 1000).toFixed(m % 1000 ? 2 : 0)}` : `${m}m`);
const mem = (b: number) => (!b ? "–" : b >= 2 ** 30 ? `${(b / 2 ** 30).toFixed(1)}Gi` : `${Math.round(b / 2 ** 20)}Mi`);
const total = computed(() => (data.value?.workloads || []).reduce((a, w) => ({ cpu: a.cpu + Math.max(0, w.savings.cpu_m), mem: a.mem + Math.max(0, w.savings.memory_bytes) }), { cpu: 0, mem: 0 }));
const kindName = (w: string) => w.split("/") as [string, string];
</script>

<template>
  <div class="rs-top">
    <div class="card rs-tile"><span class="lbl">Could free</span><b>{{ cpu(total.cpu) }} CPU · {{ mem(total.mem) }}</b><span class="hint">with the recommended requests, all pods together</span></div>
    <div class="card rs-tile"><span class="lbl">Based on</span><b>{{ data ? (data.hours >= 24 ? `${Math.round(data.hours / 24)} days` : `${data.hours} hours`) : "…" }}</b>
      <span class="hint">of 5-minute measurements<template v-if="data && data.hours < 24">. More reliable after a day or two of normal traffic.</template></span></div>
    <div class="card rs-tile"><span class="lbl">Period</span>
      <div class="segmented sm" role="group"><button v-for="d in [1, 3, 7]" :key="d" :aria-pressed="days === d" @click="days = d">{{ d }} day{{ d > 1 ? "s" : "" }}</button></div>
      <span class="hint">Recommended request = 95th percentile × 1.3 (CPU) and peak × 1.3 (memory).</span></div>
  </div>
  <Toolbar :state="state" placeholder="Search workloads or findings" :namespaces="all.map((x: any) => x.namespace)" :filters="[['all', 'All'], ['fix', 'Needs fixing'], ['save', 'Can save']]" :count="count" />
  <div v-if="error" class="banner show"><Icon name="crit" /><span class="msg">{{ error }}</span></div>
  <section class="card flush"><div class="card-body table-wrap">
    <Skeleton v-if="!data && !error" :rows="6" />
    <EmptyState v-else-if="data && !data.hours" title="Collecting usage" icon="scale" sub="k10y keeps 5-minute CPU and memory measurements. The first recommendations appear after about 10 minutes; they get better over a day or two." />
    <DataTable v-else-if="data" v-model:sort="state.sort" :rows="rows" :columns="columns" clickable empty-title="No matching workloads" empty-icon="scale"
      @row="(x) => openDetail(kindName(x.workload)[0], x.namespace, kindName(x.workload)[1])">
      <template #level="{ row }"><Badge :level="BADGE[row.level]" :label="LABEL[row.level]" /></template>
      <template #workload="{ row }"><span class="mono">{{ kindName(row.workload)[1] }}</span><div class="dim" style="font-size:12px">{{ kindName(row.workload)[0] }}</div></template>
      <template #namespace="{ row }"><span class="ns">{{ row.namespace }}</span></template>
      <template #cpu="{ row }"><span class="mono">{{ cpu(row.cpu.request) }}</span> → <b class="mono">{{ cpu(row.cpu.p95) }}</b> <span class="dim mono">/ {{ cpu(row.cpu.peak) }}</span>
        <div class="rs-rec">set request <b class="mono">{{ cpu(row.cpu.recommended) }}</b></div></template>
      <template #memory="{ row }"><span class="mono">{{ mem(row.memory.request) }} · {{ mem(row.memory.limit) }}</span> → <b class="mono">{{ mem(row.memory.peak) }}</b>
        <div class="rs-rec">set <b class="mono">{{ mem(row.memory.recommended) }}</b> · limit ≥ <b class="mono">{{ mem(row.memory.recommended_limit) }}</b></div></template>
      <template #findings="{ row }"><span v-if="!row.findings.length" class="dim">Requests fit its use.</span>
        <div v-for="f in row.findings" :key="f">{{ f }}</div></template>
      <template #savings="{ row }"><span v-if="row.savings.cpu_m > 0 || row.savings.memory_bytes > 0" class="mono">{{ row.savings.cpu_m > 0 ? cpu(row.savings.cpu_m) : "" }}{{ row.savings.cpu_m > 0 && row.savings.memory_bytes > 0 ? " · " : "" }}{{ row.savings.memory_bytes > 0 ? mem(row.savings.memory_bytes) : "" }}</span>
        <span v-else class="dim">–</span></template>
    </DataTable>
  </div></section>
</template>

<style scoped>
.rs-top { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; margin-bottom: 16px; }
.rs-tile { display: flex; flex-direction: column; gap: 6px; padding: 14px 16px; }
.rs-tile .lbl { font-size: 12px; color: var(--muted); font-weight: 550; }
.rs-tile b { font-size: 20px; font-weight: 650; }
.rs-rec { font-size: 12px; color: var(--muted); margin-top: 2px; }
.rs-rec b { color: var(--text); font-weight: 600; }
</style>
