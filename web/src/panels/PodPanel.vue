<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import Badge from "@/components/Badge.vue";
import Icon from "@/components/Icon.vue";
import LineChart from "@/components/LineChart.vue";
import LogViewer from "@/components/LogViewer.vue";
import { actDelete, startAI } from "@/lib/actions";
import { fmt, podLabel, podLevel } from "@/lib/format";
import { useCluster } from "@/stores/cluster";
import { prefs } from "@/stores/prefs";
import { useSession } from "@/stores/session";
import { openDetail, openShell, type PanelRef } from "@/stores/ui";
import EventsList from "./EventsList.vue";
import PanelHead from "./PanelHead.vue";

const props = defineProps<{ r: PanelRef }>();
const s = useSession(), c = useCluster();
const pod = computed(() => c.data?.pods.find((p: any) => p.namespace === props.r.namespace && p.name === props.r.name));
const events = computed(() => (c.data?.events || []).filter((e: any) => e.namespace === props.r.namespace && e.object === `Pod/${props.r.name}`).slice(0, 10));
const RANGES = { "5m": 5 * 60e3, "15m": 15 * 60e3, "1h": 60 * 60e3 } as const;

// live usage
const metrics = ref<any>(null);
let timer = 0;
async function poll() {
  if (!s.can("cluster.view") || c.paused || document.hidden) return;
  try { metrics.value = await c.loadMetrics("pod", { namespace: props.r.namespace, name: props.r.name }); } catch { /* agent busy */ }
}
onMounted(() => { poll(); timer = window.setInterval(poll, 5000); });
onBeforeUnmount(() => clearInterval(timer));
const cpuRefs = computed(() => [pod.value?.cpu_request_m != null && { v: pod.value.cpu_request_m, label: "request" }, pod.value?.cpu_limit_m != null && { v: pod.value.cpu_limit_m, label: "limit" }].filter(Boolean) as any[]);
const memRefs = computed(() => [pod.value?.memory_request_bytes != null && { v: pod.value.memory_request_bytes / 2 ** 20, label: "request" }, pod.value?.memory_limit_bytes != null && { v: pod.value.memory_limit_bytes / 2 ** 20, label: "limit" }].filter(Boolean) as any[]);
const millis = (v: number) => (v === 0 ? "0" : v >= 1000 ? `${(v / 1000).toFixed(2)} cores` : `${v < 10 ? v.toFixed(1) : v.toFixed(0)}m`);
const mib = (v: number) => (v === 0 ? "0" : v >= 1024 ? `${(v / 1024).toFixed(1)} GiB` : `${v >= 100 ? v.toFixed(0) : v.toFixed(1)} MiB`);

const canLogs = computed(() => s.can("logs.view"));
const canShell = computed(() => !!pod.value && pod.value.phase === "Running" && s.can("pods.exec") && c.control && !c.isProtected(props.r.namespace));
function shellNow() {
  openShell({ clusterId: s.current!.id, clusterName: s.current!.name, namespace: props.r.namespace, pod: props.r.name,
    containers: pod.value.containers, container: pod.value.containers[0] || "" });
}
const canDelete = computed(() => s.can("pods.delete") && c.control);
</script>

<template>
  <PanelHead kind="Pod" :name="r.name" :namespace="r.namespace">
    <template #status><Badge v-if="pod" :level="podLevel(pod)" :label="podLabel(pod)" /><Badge v-else level="neutral" label="Deleted" /></template>
    <template #actions>
      <button v-if="canShell" class="btn" @click="shellNow"><Icon name="code" /><span>Shell</span></button>
      <button class="btn" @click="openDetail('Pod', r.namespace, r.name, { push: true, tab: 'overview' })"><Icon name="file" /><span>Details &amp; YAML</span></button>
      <button v-if="pod && s.can('ai.use')" class="btn" @click="startAI({ scope: 'object', kind: 'Pod', namespace: r.namespace, name: r.name })"><Icon name="ai" /><span>Ask AI</span></button>
      <button v-if="pod && canDelete" class="btn" :disabled="c.isProtected(r.namespace)" @click="actDelete('pod', pod)"><Icon name="trash" /><span>Delete</span></button>
    </template>
  </PanelHead>
  <div class="drawer-body">
    <div v-if="!pod" class="empty"><Icon name="crit" /><div class="title">Pod no longer exists</div><div>It was deleted or replaced since this panel opened.</div></div>
    <template v-else>
      <div class="kv">
        <div><span>Node</span><b>{{ pod.node || "–" }}</b></div><div><span>Pod IP</span><b>{{ pod.pod_ip || "–" }}</b></div>
        <div><span>Age</span><b>{{ fmt.age(pod.age_s) }}</b></div><div><span>Ready</span><b>{{ pod.ready_containers }} / {{ pod.total_containers }}</b></div>
        <div><span>Restarts</span><b>{{ pod.restarts }}<template v-if="pod.last_termination_reasons.length"> · last: {{ pod.last_termination_reasons[0] }}</template></b></div>
        <div><span>CPU</span><b>{{ fmt.cpu(pod.cpu_usage_m) }}</b></div><div><span>Memory</span><b>{{ fmt.bytes(pod.memory_usage_bytes) }}</b></div>
        <div><span>Phase</span><b>{{ pod.phase }}</b></div>
      </div>
      <div v-if="pod.problem" class="banner show" style="margin:0"><Icon name="warn" /><span class="msg"><b>Problem detected:</b> {{ pod.problem }}</span></div>
      <div>
        <div class="section-bar" style="margin-bottom:8px"><h2>Resource usage</h2><span style="flex:1" />
          <div class="segmented sm" role="group" aria-label="Time range"><button v-for="(_, k) in RANGES" :key="k" :aria-pressed="prefs.range === k" @click="prefs.range = k">{{ k }}</button></div></div>
        <div class="two-col" style="margin:0;gap:12px">
          <div><div class="hint" style="margin-bottom:4px">CPU</div>
            <LineChart :height="170" :series="[{ label: 'CPU', color: 'var(--series-1)', points: (metrics?.points || []).map((p: any) => [p[0], p[1]]) }]" :y-fmt="millis" :refs="cpuRefs" :range="RANGES[prefs.range]" area empty-sub="No samples for this pod yet." aria-label="Pod CPU usage" /></div>
          <div><div class="hint" style="margin-bottom:4px">Memory</div>
            <LineChart :height="170" :series="[{ label: 'Memory', color: 'var(--series-2)', points: (metrics?.points || []).map((p: any) => [p[0], p[2] / 2 ** 20]) }]" :y-fmt="mib" :refs="memRefs" :range="RANGES[prefs.range]" area empty-sub="No samples for this pod yet." aria-label="Pod memory usage" /></div>
        </div>
      </div>
      <div v-if="canLogs">
        <h3 class="section-title">Logs</h3>
        <LogViewer :path="`pods/${r.namespace}/${r.name}/logs`" :containers="pod.containers" :label="`${r.namespace}-${r.name}`" />
      </div>
      <div>
        <h3 class="section-title">Containers</h3>
        <ul class="list card" style="box-shadow:none"><li v-for="(x, i) in pod.containers" :key="x"><span class="ic"><Icon name="pods" /></span>
          <div style="min-width:0"><div class="main-line">{{ x }}</div><div class="sub-line" style="font-family:var(--mono)">{{ pod.images[i] }}</div></div></li></ul>
      </div>
      <div v-if="events.length"><h3 class="section-title">Events</h3><EventsList :events="events" /></div>
    </template>
  </div>
</template>
