<script setup lang="ts">
import { reactive, watch } from "vue";
import Icon from "@/components/Icon.vue";
import Modal from "@/components/Modal.vue";
import { BALANCE, type HxBackend, type HxOp } from "@/lib/haproxy";

const props = defineProps<{ open: boolean; backend: HxBackend | null }>();
const emit = defineEmits<{ close: []; review: [ops: HxOp[], title: string] }>();
const f = reactive({ name: "", mode: "http", balance: "roundrobin", check: "http", uri: "/", servers: [] as { name: string; address: string; port: number | string }[], err: "" });
watch(() => props.open, (o) => {
  if (!o) return;
  const b = props.backend;
  Object.assign(f, { name: b?.name || "", mode: b?.mode || "http", balance: b?.balance || "roundrobin", err: "",
    check: b ? (b.check.type === "default" ? "none" : b.check.type) : "http", uri: b?.check.uri || "/", servers: b ? [] : [{ name: "", address: "", port: 80 }] });
});
function submit() {
  f.err = "";
  if (!f.name.trim()) { f.err = "Give the backend a name."; return; }
  const name = f.name.trim();
  const b = props.backend, data: Record<string, any> = {};
  const check = { type: f.check === "none" ? "default" : f.check, uri: f.check === "http" ? f.uri : "" };
  if (!b || f.mode !== b.mode) data.mode = f.mode;
  if (!b || f.balance !== b.balance) data.balance = f.balance;
  if (!b || check.type !== b.check.type || (check.type === "http" && check.uri !== b.check.uri)) data.check = check;
  if (b && !Object.keys(data).length) { f.err = "Nothing changed."; return; }
  const ops: HxOp[] = [{ op: b ? "update" : "create", kind: "backend", name, data }];
  for (const s of f.servers.filter((x) => x.name || x.address))
    ops.push({ op: "create", kind: "server", backend: name, name: s.name.trim(), data: { address: s.address.trim(), port: Number(s.port), check: f.check !== "none" } });
  emit("review", ops, props.backend ? `Edit backend ${name}` : `New backend ${name}`);
}
</script>
<template>
  <Modal :open="open" :title="backend ? `Edit backend ${backend.name}` : 'New backend'" wide @close="emit('close')">
    <p class="hint" style="margin:0">A backend is a group of servers that share the traffic, with a balancing method and a health check.</p>
    <div class="two-col">
      <div class="form-row"><label class="lbl">Name</label><input v-model="f.name" class="input" :disabled="!!backend" placeholder="e.g. shop" style="font-family:var(--mono)"></div>
      <div class="form-row"><label class="lbl">Protocol</label><div class="segmented" role="group">
        <button type="button" :aria-pressed="f.mode === 'http'" @click="f.mode = 'http'">HTTP</button><button type="button" :aria-pressed="f.mode === 'tcp'" @click="f.mode = 'tcp'">TCP</button></div></div>
      <div class="form-row"><label class="lbl">Balancing</label><select v-model="f.balance" class="select"><option v-for="[v, l] in BALANCE" :key="v" :value="v">{{ l }}</option></select></div>
      <div class="form-row"><label class="lbl">Health check</label><select v-model="f.check" class="select">
        <option value="http">HTTP request</option><option value="tcp">TCP connect</option><option value="none">Default (connect only)</option></select></div>
    </div>
    <div v-if="f.check === 'http'" class="form-row"><label class="lbl">Health check path</label><input v-model="f.uri" class="input" placeholder="/health" style="font-family:var(--mono)">
      <span class="hint">HAProxy sends GET to this path; 2xx or 3xx means healthy.</span></div>
    <div v-if="!backend" class="form-row"><label class="lbl">Servers</label>
      <div v-for="(s, i) in f.servers" :key="i" class="assign-row">
        <input v-model="s.name" class="input" placeholder="name" style="max-width:140px;font-family:var(--mono)" aria-label="Server name">
        <input v-model="s.address" class="input" placeholder="host or IP" style="font-family:var(--mono)" aria-label="Address">
        <span class="hint">:</span><input v-model="s.port" class="input" type="number" min="1" max="65535" style="max-width:100px" aria-label="Port">
        <button class="btn icon-only ghost" type="button" aria-label="Remove server" @click="f.servers.splice(i, 1)"><Icon name="x" /></button></div>
      <button class="btn sm" type="button" style="align-self:flex-start" @click="f.servers.push({ name: '', address: '', port: 80 })">Add server</button></div>
    <div v-if="f.err" class="test-result show err"><Icon name="crit" /><span>{{ f.err }}</span></div>
    <template #actions><button class="btn ghost" type="button" @click="emit('close')">Cancel</button><span style="flex:1" />
      <button class="btn primary" type="button" @click="submit">Review change</button></template>
  </Modal>
</template>
