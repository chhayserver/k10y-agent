<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { agent } from "@/api/client";
import Badge from "@/components/Badge.vue";
import Chips from "@/components/Chips.vue";
import Icon from "@/components/Icon.vue";
import RowMenu from "@/components/RowMenu.vue";
import Skeleton from "@/components/Skeleton.vue";
import { menuFor } from "@/lib/actions";
import { DETAIL_KINDS, fmt } from "@/lib/format";
import { useSession } from "@/stores/session";
import type { PanelRef } from "@/stores/ui";
import EventsList from "./EventsList.vue";
import OpenLink from "./OpenLink.vue";
import PanelHead from "./PanelHead.vue";

const props = defineProps<{ r: PanelRef }>();
const s = useSession();
const tab = ref(props.r.tab || "overview");
const d = ref<any>(null), error = ref("");
async function load() {
  d.value = null; error.value = "";
  try { d.value = await agent(s.current!.id).get("resource", { kind: props.r.kind, name: props.r.name, namespace: props.r.namespace || undefined }); }
  catch (e: any) { error.value = e.message; }
}
onMounted(load);

const o = computed(() => d.value?.object || {});
const meta = computed(() => o.value.metadata || {});
const sp = computed(() => o.value.spec || {});
const st = computed(() => o.value.status || {});
const tmpl = computed(() => sp.value.template?.spec);
const tmplLabels = computed(() => sp.value.template?.metadata?.labels);
const ann = computed(() => Object.entries<string>(meta.value.annotations || {}));
const menuKind: Record<string, string> = { Deployment: "deployment", StatefulSet: "statefulset", DaemonSet: "daemonset", Job: "job", Service: "service", Node: "node", ConfigMap: "configmap", Secret: "secret", Pod: "pod" };

const badge = computed<[string, string] | null>(() => {
  const k = props.r.kind, x = st.value, y = sp.value;
  const cond = (t: string) => (x.conditions || []).find((c: any) => c.type === t);
  if (["Deployment", "StatefulSet", "ReplicaSet"].includes(k)) { const want = y.replicas ?? 0, ready = x.readyReplicas || 0; return want === 0 ? ["neutral", "Scaled to 0"] : [ready >= want ? "good" : "warning", `${ready}/${want} ready`]; }
  if (k === "DaemonSet") return [(x.numberReady || 0) >= (x.desiredNumberScheduled || 0) ? "good" : "warning", `${x.numberReady || 0}/${x.desiredNumberScheduled || 0} ready`];
  if (k === "Node") { const r = cond("Ready"); return r?.status === "True" ? ["good", y.unschedulable ? "Ready, cordoned" : "Ready"] : ["critical", "NotReady"]; }
  if (k === "Pod") return [x.phase === "Running" ? "good" : x.phase === "Succeeded" ? "neutral" : x.phase === "Failed" ? "critical" : "warning", x.phase || "Unknown"];
  if (k === "Job") return x.succeeded ? ["good", "Complete"] : x.failed ? ["critical", "Failed"] : ["warning", `${x.active || 0} active`];
  if (k === "PersistentVolumeClaim" || k === "Namespace") return [x.phase === "Bound" || x.phase === "Active" ? "good" : "warning", x.phase || "Unknown"];
  if (k === "Service") return ["neutral", y.type || "ClusterIP"];
  if (k === "Secret") return ["neutral", o.value.type || "Opaque"];
  return null;
});
const qty = (r?: Record<string, string>) => (r ? Object.entries(r).map(([k, v]) => `${k} ${v}`).join(", ") : "–");
const ports = (c: any) => (c.ports || []).map((p: any) => `${p.name ? `${p.name}:` : ""}${p.containerPort}/${p.protocol || "TCP"}`).join(", ") || "–";
const probes = (c: any) => [c.livenessProbe && "liveness", c.readinessProbe && "readiness", c.startupProbe && "startup"].filter(Boolean).join(", ") || "–";
const stateOf = (name: string, list?: any[]) => {
  const s0 = (list || []).find((x) => x.name === name);
  if (!s0) return null;
  const [k, v] = Object.entries<any>(s0.state || {})[0] || ["unknown", {}];
  return { text: `${k}${v?.reason ? `: ${v.reason}` : ""}`, restarts: s0.restartCount };
};
const condLevel = (c: any) => {
  const bad = ["MemoryPressure", "DiskPressure", "PIDPressure", "NetworkUnavailable", "ReplicaFailure", "Failed"].includes(c.type);
  if (c.status === "True") return bad ? "critical" : "good";
  if (c.status === "False") return bad ? "good" : "warning";
  return "neutral";
};
const related = computed(() => {
  const g: Record<string, any[]> = {};
  for (const x of d.value?.related || []) (g[x.relation] = g[x.relation] || []).push(x);
  return g;
});
const host = computed(() => location.hostname);
const copied = ref(false);
async function copyYaml() { try { await navigator.clipboard.writeText(d.value.yaml); copied.value = true; setTimeout(() => (copied.value = false), 1500); } catch { /* denied */ } }
</script>

<template>
  <PanelHead :kind="r.kind" :name="r.name" :namespace="r.namespace">
    <template #status><Badge v-if="badge" :level="badge[0]" :label="badge[1]" /></template>
    <template #actions>
      <button class="btn icon-only ghost" aria-label="Reload" title="Reload" @click="load"><Icon name="refresh" /></button>
      <RowMenu v-if="menuKind[r.kind] && d" :items="() => menuFor(menuKind[r.kind], { ...o, namespace: r.namespace, name: r.name, desired: sp.replicas, ready: st.readyReplicas, unschedulable: sp.unschedulable, containers: (tmpl?.containers || []).map((c: any) => ({ name: c.name, image: c.image })) })" :label="r.name" />
    </template>
  </PanelHead>
  <div style="padding:10px 20px 0"><div class="segmented" role="tablist">
    <button v-for="t in [['overview', 'Overview'], ['yaml', 'YAML'], ['events', `Events${d ? ` · ${d.events.length}` : ''}`]]" :key="t[0]" role="tab" :aria-pressed="tab === t[0]" :aria-selected="tab === t[0]" @click="tab = t[0]">{{ t[1] }}</button>
  </div></div>
  <div class="drawer-body">
    <div v-if="error" class="empty"><Icon name="crit" /><div class="title">Could not load {{ r.kind }}</div><div>{{ error }}</div></div>
    <Skeleton v-else-if="!d" :rows="8" />
    <template v-else-if="tab === 'yaml'">
      <div style="display:flex;gap:8px;align-items:center"><span class="hint" style="flex:1">{{ r.kind === "Secret" ? "Secret values are hidden." : "Live object, read-only. managedFields omitted." }}</span>
        <button class="btn sm" @click="copyYaml"><Icon name="copy" /><span>{{ copied ? "Copied" : "Copy" }}</span></button></div>
      <pre class="logs" style="max-height:none;white-space:pre">{{ d.yaml }}</pre>
    </template>
    <EventsList v-else-if="tab === 'events'" :events="d.events" />
    <template v-else>
      <div class="kv">
        <div><span>Created</span><b>{{ meta.creationTimestamp ? new Date(meta.creationTimestamp).toLocaleString() : "–" }} <span class="dim">({{ fmt.ago(meta.creationTimestamp) }})</span></b></div>
        <div v-if="meta.namespace"><span>Namespace</span><b><OpenLink kind="Namespace" :name="meta.namespace" /></b></div>
        <div v-if="meta.ownerReferences?.length"><span>Controlled by</span><b><OpenLink v-for="x in meta.ownerReferences" :key="x.name" :kind="x.kind" :namespace="meta.namespace" :name="x.name" :label="`${x.kind}/${x.name}`" /></b></div>
        <div><span>UID</span><b style="font-family:var(--mono);font-size:12px">{{ meta.uid || "–" }}</b></div>
      </div>
      <div><h3 class="section-title">Labels</h3>
        <Chips v-if="meta.labels && Object.keys(meta.labels).length" :items="meta.labels" />
        <span v-else class="hint">No labels on this {{ r.kind }} object itself.<template v-if="tmplLabels"> Its pods' labels are under <b>Pod template labels</b> below.</template></span></div>
      <details v-if="ann.length"><summary class="section-title" style="cursor:pointer;margin:0">Annotations · {{ ann.length }}</summary>
        <div style="display:grid;gap:6px;margin-top:8px"><div v-for="[k, v] in ann" :key="k" style="font-size:12px"><b style="font-family:var(--mono)">{{ k }}</b>
          <div class="hint" style="font-family:var(--mono);overflow-wrap:anywhere">{{ String(v).length > 400 ? `${String(v).slice(0, 400)}…` : v }}</div></div></div></details>

      <div><h3 class="section-title">{{ r.kind }}</h3>
        <div class="kv">
          <template v-if="r.kind === 'Deployment'">
            <div><span>Replicas</span><b>{{ sp.replicas ?? 0 }} desired · {{ st.updatedReplicas || 0 }} updated · {{ st.readyReplicas || 0 }} ready · {{ st.availableReplicas || 0 }} available</b></div>
            <div><span>Strategy</span><b>{{ sp.strategy?.type || "RollingUpdate" }}<template v-if="sp.strategy?.rollingUpdate"> (max surge {{ sp.strategy.rollingUpdate.maxSurge }}, max unavailable {{ sp.strategy.rollingUpdate.maxUnavailable }})</template></b></div>
            <div><span>Revision</span><b>{{ meta.annotations?.["deployment.kubernetes.io/revision"] || "–" }}</b></div>
            <div><span>Selector</span><b><Chips :items="sp.selector?.matchLabels" /></b></div>
          </template>
          <template v-else-if="r.kind === 'StatefulSet'">
            <div><span>Replicas</span><b>{{ sp.replicas ?? 0 }} desired · {{ st.readyReplicas || 0 }} ready · {{ st.currentReplicas || 0 }} current</b></div>
            <div><span>Service name</span><b>{{ sp.serviceName || "–" }}</b></div><div><span>Update strategy</span><b>{{ sp.updateStrategy?.type || "RollingUpdate" }}</b></div>
            <div><span>Selector</span><b><Chips :items="sp.selector?.matchLabels" /></b></div>
          </template>
          <template v-else-if="r.kind === 'DaemonSet'">
            <div><span>Pods</span><b>{{ st.desiredNumberScheduled || 0 }} desired · {{ st.numberReady || 0 }} ready · {{ st.updatedNumberScheduled || 0 }} updated</b></div>
            <div><span>Update strategy</span><b>{{ sp.updateStrategy?.type || "RollingUpdate" }}</b></div><div><span>Selector</span><b><Chips :items="sp.selector?.matchLabels" /></b></div>
          </template>
          <template v-else-if="r.kind === 'ReplicaSet'">
            <div><span>Replicas</span><b>{{ sp.replicas ?? 0 }} desired · {{ st.readyReplicas || 0 }} ready</b></div>
            <div><span>Revision</span><b>{{ meta.annotations?.["deployment.kubernetes.io/revision"] || "–" }}</b></div>
          </template>
          <template v-else-if="r.kind === 'Job'">
            <div><span>Completions</span><b>{{ st.succeeded || 0 }} / {{ sp.completions ?? 1 }}</b></div><div><span>Parallelism</span><b>{{ sp.parallelism ?? 1 }}</b></div>
            <div><span>Active · failed</span><b>{{ st.active || 0 }} · {{ st.failed || 0 }}</b></div><div><span>Started</span><b>{{ fmt.ago(st.startTime) }}</b></div>
            <div><span>Backoff limit</span><b>{{ sp.backoffLimit ?? 6 }}</b></div>
          </template>
          <template v-else-if="r.kind === 'Pod'">
            <div><span>Phase</span><b>{{ st.phase }}</b></div><div><span>Node</span><b><OpenLink v-if="sp.nodeName" kind="Node" :name="sp.nodeName" /></b></div>
            <div><span>Pod IP</span><b>{{ st.podIP || "–" }}</b></div><div><span>QoS class</span><b>{{ st.qosClass || "–" }}</b></div>
            <div><span>Service account</span><b>{{ sp.serviceAccountName || "–" }}</b></div><div><span>Started</span><b>{{ fmt.ago(st.startTime) }}</b></div>
          </template>
          <template v-else-if="r.kind === 'Service'">
            <div><span>Type</span><b>{{ sp.type || "ClusterIP" }}</b></div><div><span>Cluster IP</span><b style="font-family:var(--mono)">{{ (sp.clusterIPs || [sp.clusterIP]).join(", ") }}</b></div>
            <div><span>Selector</span><b><Chips :items="sp.selector" /></b></div><div><span>Session affinity</span><b>{{ sp.sessionAffinity || "None" }}</b></div>
          </template>
          <template v-else-if="r.kind === 'Node'">
            <div><span>Addresses</span><b>{{ (st.addresses || []).map((a: any) => `${a.type} ${a.address}`).join(" · ") }}</b></div>
            <div><span>OS image</span><b>{{ st.nodeInfo?.osImage }}</b></div><div><span>Kernel</span><b>{{ st.nodeInfo?.kernelVersion }}</b></div>
            <div><span>Container runtime</span><b>{{ st.nodeInfo?.containerRuntimeVersion }}</b></div><div><span>Kubelet</span><b>{{ st.nodeInfo?.kubeletVersion }}</b></div>
            <div><span>Architecture</span><b>{{ st.nodeInfo?.architecture }}</b></div><div><span>Pod CIDR</span><b>{{ sp.podCIDR || "–" }}</b></div>
            <div><span>Schedulable</span><b>{{ sp.unschedulable ? "No (cordoned)" : "Yes" }}</b></div>
          </template>
          <template v-else-if="r.kind === 'PersistentVolumeClaim'">
            <div><span>Phase</span><b>{{ st.phase }}</b></div><div><span>Capacity</span><b>{{ qty(st.capacity) }}</b></div>
            <div><span>Access modes</span><b>{{ (sp.accessModes || []).join(", ") }}</b></div><div><span>Storage class</span><b>{{ sp.storageClassName || "–" }}</b></div>
          </template>
          <template v-else-if="r.kind === 'Ingress'"><div><span>Class</span><b>{{ sp.ingressClassName || "–" }}</b></div></template>
          <template v-else-if="r.kind === 'ConfigMap' || r.kind === 'Secret'"><div><span>Keys</span><b><Chips :keys="Object.keys(o.data || {})" /></b></div></template>
          <div v-if="tmplLabels"><span>Pod template labels</span><b><Chips :items="tmplLabels" /></b></div>
        </div>
      </div>

      <div v-if="tmpl?.containers || sp.containers">
        <h3 class="section-title">Containers</h3>
        <div class="table-wrap card" style="box-shadow:none"><table class="data"><thead><tr><th>Name</th><th>Image</th><th>Ports</th><th>Requests</th><th>Limits</th><th>Probes</th>
          <th v-if="r.kind === 'Pod'">State</th><th v-if="r.kind === 'Pod'">Restarts</th></tr></thead><tbody>
          <tr v-for="x in tmpl?.containers || sp.containers" :key="x.name"><td class="primary">{{ x.name }}</td><td class="mono" style="white-space:normal;overflow-wrap:anywhere">{{ x.image }}</td>
            <td>{{ ports(x) }}</td><td>{{ qty(x.resources?.requests) }}</td><td>{{ qty(x.resources?.limits) }}</td><td>{{ probes(x) }}</td>
            <template v-if="r.kind === 'Pod'"><td>{{ stateOf(x.name, st.containerStatuses)?.text }}</td><td class="num">{{ stateOf(x.name, st.containerStatuses)?.restarts ?? "–" }}</td></template></tr>
        </tbody></table></div>
      </div>
      <div v-if="r.kind === 'Service'">
        <h3 class="section-title">Ports</h3>
        <div class="table-wrap card" style="box-shadow:none"><table class="data"><thead><tr><th>Name</th><th>Port</th><th>Target</th><th>Protocol</th><th>NodePort</th></tr></thead><tbody>
          <tr v-for="p in sp.ports || []" :key="p.port"><td>{{ p.name || "–" }}</td><td class="num">{{ p.port }}</td><td>{{ p.targetPort }}</td><td>{{ p.protocol || "TCP" }}</td>
            <td><a v-if="p.nodePort" class="ext-link" :href="`http://${host}:${p.nodePort}`" target="_blank" rel="noopener">{{ p.nodePort }}<Icon name="external" /></a><span v-else>–</span></td></tr>
        </tbody></table></div>
      </div>
      <div v-if="r.kind === 'Node'">
        <h3 class="section-title">Capacity</h3>
        <div class="table-wrap card" style="box-shadow:none"><table class="data"><thead><tr><th>Resource</th><th>Capacity</th><th>Allocatable</th></tr></thead><tbody>
          <tr v-for="k in Object.keys(st.capacity || {}).sort()" :key="k"><td>{{ k }}</td><td>{{ st.capacity[k] }}</td><td>{{ st.allocatable?.[k] || "–" }}</td></tr></tbody></table></div>
        <h3 class="section-title" style="margin-top:18px">Taints</h3>
        <span v-if="!(sp.taints || []).length" class="dim">None</span><span v-for="t in sp.taints || []" :key="t.key" class="chip">{{ t.key }}{{ t.value ? `=${t.value}` : "" }}:{{ t.effect }}</span>
      </div>
      <div v-if="r.kind === 'Ingress'">
        <h3 class="section-title">Rules</h3>
        <div class="table-wrap card" style="box-shadow:none"><table class="data"><thead><tr><th>Host</th><th>Path</th><th>Service</th></tr></thead><tbody>
          <template v-for="rule in sp.rules || []" :key="rule.host"><tr v-for="p in rule.http?.paths || []" :key="p.path"><td>{{ rule.host || "*" }}</td><td>{{ p.path || "/" }}</td>
            <td><OpenLink v-if="p.backend?.service" kind="Service" :namespace="meta.namespace" :name="p.backend.service.name" /></td></tr></template></tbody></table></div>
      </div>

      <div v-if="st.conditions?.length">
        <h3 class="section-title">Conditions</h3>
        <div class="table-wrap card" style="box-shadow:none"><table class="data"><thead><tr><th>Type</th><th>Status</th><th>Reason</th><th>Message</th><th>Last change</th></tr></thead><tbody>
          <tr v-for="x in st.conditions" :key="x.type"><td class="primary">{{ x.type }}</td><td><Badge :level="condLevel(x)" :label="x.status" /></td><td>{{ x.reason || "–" }}</td>
            <td class="wrap">{{ x.message }}</td><td>{{ fmt.ago(x.lastTransitionTime || x.lastHeartbeatTime) }}</td></tr></tbody></table></div>
      </div>

      <div v-if="Object.keys(related).length || d.pods">
        <h3 class="section-title">Related</h3>
        <div v-for="(items, rel) in related" :key="rel" class="rel"><h4>{{ rel }} · {{ items.length }}</h4>
          <OpenLink v-for="x in items" :key="`${x.kind}/${x.name}`" :kind="x.kind" :namespace="x.namespace" :name="x.name" :extra="x.status" /></div>
        <div v-if="d.pods" class="rel"><h4>Pods · {{ d.pods.length }}</h4>
          <div v-if="d.pods.length" class="table-wrap card" style="box-shadow:none"><table class="data"><thead><tr><th>Name</th><th>Status</th><th>Ready</th><th>Restarts</th><th>Node</th><th>Age</th></tr></thead><tbody>
            <tr v-for="p in d.pods.slice(0, 100)" :key="`${p.namespace}/${p.name}`"><td class="primary"><OpenLink kind="Pod" :namespace="p.namespace" :name="p.name" /></td>
              <td><Badge :level="p.problem ? 'critical' : p.phase === 'Running' ? 'good' : p.phase === 'Succeeded' ? 'neutral' : 'warning'" :label="p.phase === 'Succeeded' ? 'Completed' : p.status" /></td>
              <td class="num">{{ p.ready_containers }}/{{ p.total_containers }}</td><td class="num">{{ p.restarts }}</td><td>{{ p.node || "–" }}</td><td class="num">{{ fmt.age(p.age_s) }}</td></tr></tbody></table></div>
          <p v-else class="hint" style="margin:0">No pods.</p></div>
      </div>
      <span v-if="!DETAIL_KINDS.has(r.kind)" />
    </template>
  </div>
</template>
