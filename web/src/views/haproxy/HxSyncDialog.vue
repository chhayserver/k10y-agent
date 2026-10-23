<script setup lang="ts">
// Keep a backend's servers in sync with a Kubernetes Service.
import { computed, reactive, ref, watch } from "vue";
import { agent } from "@/api/client";
import Icon from "@/components/Icon.vue";
import Modal from "@/components/Modal.vue";
import { fmt } from "@/lib/format";
import { hx, type HxBackend } from "@/lib/haproxy";
import { useSession } from "@/stores/session";
import { toast } from "@/stores/ui";

const props = defineProps<{ open: boolean; instanceId: number; backend: HxBackend | null }>();
const emit = defineEmits<{ close: []; changed: [] }>();
const s = useSession();
const f = reactive({ cluster_id: 0, namespace: "", service: "", port: "", mode: "nodeport", enabled: true, busy: false, msg: "", ok: true });
const services = ref<any[]>([]);
watch(() => props.open, (o) => {
  if (!o) return;
  const x = props.backend?.k8s_sync;
  Object.assign(f, { cluster_id: x?.cluster_id || s.current?.id || s.clusters[0]?.id || 0, namespace: x?.namespace || "", service: x?.service || "",
    port: x?.port || "", mode: x?.mode || "nodeport", enabled: x?.enabled ?? true, busy: false, msg: x ? (x.last_error || x.last_result) : "", ok: !x?.last_error });
  loadServices();
});
watch(() => f.cluster_id, loadServices);
async function loadServices() {
  if (!f.cluster_id) return;
  try { services.value = await agent(f.cluster_id).get("services"); } catch { services.value = []; }
}
const namespaces = computed(() => [...new Set(services.value.map((x) => x.namespace))].sort());
const svc = computed(() => services.value.find((x) => x.namespace === f.namespace && x.name === f.service));
async function save() {
  f.busy = true; f.msg = "";
  try {
    const r = await hx.setSync(props.instanceId, props.backend!.name, { cluster_id: f.cluster_id, namespace: f.namespace, service: f.service, port: f.port, mode: f.mode, enabled: f.enabled });
    f.ok = !r.last_error; f.msg = r.last_error || r.last_result || r.message;
    if (f.ok) { toast(true, `Sync saved: ${f.msg}`); emit("changed"); }
  } catch (e: any) { f.ok = false; f.msg = e.message; } finally { f.busy = false; }
}
async function remove() {
  if (!confirm("Stop syncing? The current servers stay in the backend.")) return;
  await hx.deleteSync(props.instanceId, props.backend!.name); toast(true, "Sync removed"); emit("changed"); emit("close");
}
</script>
<template>
  <Modal :open="open" :title="`Sync ${backend?.name} from Kubernetes`" wide @close="emit('close')">
    <p class="hint" style="margin:0">k10y keeps this backend's servers matching a Kubernetes Service, checking every 30 seconds. Servers it manages are named <code>k8s-…</code>; other servers in the backend are left alone. Every change is validated, applied with a graceful reload, and recorded in Config history.</p>
    <div class="two-col">
      <div class="form-row"><label class="lbl">Cluster</label><select v-model="f.cluster_id" class="select"><option v-for="c in s.clusters" :key="c.id" :value="c.id">{{ c.name }}</option></select></div>
      <div class="form-row"><label class="lbl">Namespace</label><select v-model="f.namespace" class="select"><option value="" disabled>Choose…</option><option v-for="n in namespaces" :key="n">{{ n }}</option></select></div>
      <div class="form-row"><label class="lbl">Service</label><select v-model="f.service" class="select"><option value="" disabled>Choose…</option>
        <option v-for="x in services.filter((y) => y.namespace === f.namespace)" :key="x.name" :value="x.name">{{ x.name }} ({{ x.type }})</option></select></div>
      <div class="form-row"><label class="lbl">Port</label><select v-model="f.port" class="select"><option value="">First port</option>
        <option v-for="p in svc?.ports || []" :key="p.port" :value="String(p.name || p.port)">{{ p.name || p.port }} · {{ p.port }}{{ p.node_port ? ` → node ${p.node_port}` : "" }}</option></select></div>
    </div>
    <div class="form-row"><label class="lbl">Send traffic to</label>
      <div class="segmented" role="group"><button type="button" :aria-pressed="f.mode === 'nodeport'" @click="f.mode = 'nodeport'">Node ports</button>
        <button type="button" :aria-pressed="f.mode === 'pods'" @click="f.mode = 'pods'">Pod IPs</button></div>
      <span class="hint">{{ f.mode === "nodeport" ? "Each ready node's IP with the service's NodePort. Works when HAProxy runs outside the cluster." : "Each ready pod's IP and target port. Only when HAProxy can reach pod IPs (same network, or running inside the cluster)." }}</span></div>
    <label class="pill" style="align-self:flex-start"><input v-model="f.enabled" type="checkbox"> Keep in sync automatically</label>
    <div v-if="backend?.k8s_sync?.last_sync_at" class="hint">Last sync {{ fmt.ago(backend.k8s_sync.last_sync_at) }}</div>
    <div v-if="f.msg" class="test-result show" :class="f.ok ? 'ok' : 'err'"><Icon :name="f.ok ? 'ok' : 'crit'" /><span>{{ f.msg }}</span></div>
    <template #actions><button class="btn ghost" type="button" @click="emit('close')">Close</button>
      <button v-if="backend?.k8s_sync" class="btn ghost" type="button" style="color:var(--critical)" @click="remove">Stop syncing</button><span style="flex:1" />
      <button class="btn primary" type="button" :disabled="f.busy || !f.cluster_id || !f.namespace || !f.service" @click="save">{{ f.busy ? "Syncing…" : "Save and sync now" }}</button></template>
  </Modal>
</template>
