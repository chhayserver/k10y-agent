<script setup lang="ts">
// Create or edit a service (an HAProxy frontend): where it listens, and where requests go.
import { reactive, watch } from "vue";
import Icon from "@/components/Icon.vue";
import Modal from "@/components/Modal.vue";
import { ROUTE_KIND, type HxBackend, type HxFrontend, type HxOp } from "@/lib/haproxy";

const props = defineProps<{ open: boolean; frontend: HxFrontend | null; backends: HxBackend[] }>();
const emit = defineEmits<{ close: []; review: [ops: HxOp[], title: string] }>();
const f = reactive({ name: "", mode: "http", binds: [] as { address: string; port: number | string }[], default_backend: "", routes: [] as { kind: string; value: string; backend: string }[], err: "" });

watch(() => props.open, (o) => {
  if (!o) return;
  const fe = props.frontend;
  Object.assign(f, {
    name: fe?.name || "", mode: fe?.mode || "http", default_backend: fe?.default_backend || props.backends[0]?.name || "", err: "",
    binds: fe ? fe.binds.map((b) => ({ address: b.address, port: b.port })) : [{ address: "*", port: 80 }],
    routes: fe && fe.simple_routes ? fe.routes.map((r) => ({ ...r })) : [],
  });
});
function submit() {
  f.err = "";
  if (!f.name.trim()) { f.err = "Give the service a name."; return; }
  if (!f.binds.length) { f.err = "Add at least one listen address."; return; }
  // Only send what changed, so the config diff shows exactly the edit and nothing else.
  const fe = props.frontend, data: Record<string, any> = {};
  const binds = f.binds.map((b) => ({ address: (b.address || "*").trim(), port: Number(b.port) }));
  const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
  if (!fe || f.mode !== fe.mode) data.mode = f.mode;
  if (!fe || !same(binds, fe.binds.map((b) => ({ address: b.address, port: b.port })))) data.binds = binds;
  if (!fe || (f.default_backend || null) !== fe.default_backend) data.default_backend = f.default_backend || null;
  const routes = f.mode === "http" ? f.routes.map((r) => ({ kind: r.kind, value: r.value.trim(), backend: r.backend })) : [];
  if ((!fe || fe.simple_routes) && (!fe || !same(routes, fe.routes.map((r) => ({ kind: r.kind, value: r.value, backend: r.backend }))))) data.routes = routes;
  if (fe && !Object.keys(data).length) { f.err = "Nothing changed."; return; }
  const op: HxOp = { op: props.frontend ? "update" : "create", kind: "frontend", name: f.name.trim(), data };
  emit("review", [op], props.frontend ? `Edit service ${f.name}` : `New service ${f.name}`);
}
</script>
<template>
  <Modal :open="open" :title="frontend ? `Edit service ${frontend.name}` : 'New service'" wide @close="emit('close')">
    <p class="hint" style="margin:0">A service is an HAProxy <b>frontend</b>: the address and port clients connect to, and the rules that pick a backend.</p>
    <div class="two-col">
      <div class="form-row"><label class="lbl">Name</label><input v-model="f.name" class="input" :disabled="!!frontend" placeholder="e.g. web" style="font-family:var(--mono)"></div>
      <div class="form-row"><label class="lbl">Protocol</label><div class="segmented" role="group">
        <button type="button" :aria-pressed="f.mode === 'http'" @click="f.mode = 'http'">HTTP</button><button type="button" :aria-pressed="f.mode === 'tcp'" @click="f.mode = 'tcp'">TCP</button></div></div>
    </div>
    <div class="form-row"><label class="lbl">Listen on</label>
      <div v-for="(b, i) in f.binds" :key="i" class="assign-row">
        <input v-model="b.address" class="input" placeholder="* (all addresses)" style="font-family:var(--mono);max-width:220px" aria-label="Address">
        <span class="hint">:</span><input v-model="b.port" class="input" type="number" min="1" max="65535" style="max-width:110px" aria-label="Port">
        <button class="btn icon-only ghost" type="button" aria-label="Remove listen address" :disabled="f.binds.length === 1" @click="f.binds.splice(i, 1)"><Icon name="x" /></button></div>
      <button class="btn sm" type="button" style="align-self:flex-start" @click="f.binds.push({ address: '*', port: '' })">Add address</button></div>
    <div class="form-row"><label class="lbl">Default backend</label>
      <select v-model="f.default_backend" class="select"><option value="">None</option><option v-for="b in backends" :key="b.name" :value="b.name">{{ b.name }}</option></select>
      <span class="hint">Requests that match no route go here.</span></div>
    <div v-if="f.mode === 'http'" class="form-row"><label class="lbl">Routes</label>
      <template v-if="!frontend || frontend.simple_routes">
        <div v-for="(r, i) in f.routes" :key="i" class="assign-row">
          <select v-model="r.kind" class="select" style="max-width:170px" aria-label="Match"><option v-for="k in ['path_prefix', 'path', 'host']" :key="k" :value="k">{{ ROUTE_KIND[k] }}</option></select>
          <input v-model="r.value" class="input" :placeholder="r.kind === 'host' ? 'api.example.com' : '/api'" style="font-family:var(--mono)" aria-label="Value">
          <span class="hint">→</span>
          <select v-model="r.backend" class="select" style="max-width:180px" aria-label="Backend"><option v-for="b in backends" :key="b.name" :value="b.name">{{ b.name }}</option></select>
          <button class="btn icon-only ghost" type="button" aria-label="Remove route" @click="f.routes.splice(i, 1)"><Icon name="x" /></button></div>
        <button class="btn sm" type="button" style="align-self:flex-start" @click="f.routes.push({ kind: 'path_prefix', value: '', backend: backends[0]?.name || '' })">Add route</button>
        <span class="hint">Checked from top to bottom; the first match wins.</span>
      </template>
      <div v-else class="test-result show"><Icon name="warn" /><span>This service has custom routing rules that this form can't edit safely; they're kept as they are. Edit them on the <b>Config</b> tab.</span></div>
    </div>
    <div v-if="f.err" class="test-result show err"><Icon name="crit" /><span>{{ f.err }}</span></div>
    <template #actions><button class="btn ghost" type="button" @click="emit('close')">Cancel</button><span style="flex:1" />
      <button class="btn primary" type="button" @click="submit">Review change</button></template>
  </Modal>
</template>
