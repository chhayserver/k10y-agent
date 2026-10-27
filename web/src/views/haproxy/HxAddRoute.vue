<script setup lang="ts">
// Add a route to a frontend in one step: a path, and either a new backend (with its servers) or an existing one.
// Creates the backend and updates the frontend's routes in a single reviewed change.
import { computed, reactive, watch } from "vue";
import Icon from "@/components/Icon.vue";
import Modal from "@/components/Modal.vue";
import type { HxBackend, HxFrontend, HxOp } from "@/lib/haproxy";
import { USE_CASES, backendChanges, backendData, splitHostPort, type Settings } from "@/lib/haproxyCatalog";
import HxBackendSettings from "./HxBackendSettings.vue";

const props = defineProps<{ open: boolean; frontend: HxFrontend | null; backends: HxBackend[] }>();
const emit = defineEmits<{ close: []; review: [ops: HxOp[], title: string] }>();
const f = reactive({ kind: "path_prefix", path: "", target: "new", existing: "", name: "", check: "", servers: [""] as string[],
  settings: backendData(USE_CASES[2]) as Settings, err: "" });
watch(() => props.open, (o) => {
  if (!o) return;
  Object.assign(f, { kind: "path_prefix", path: "", target: "new", existing: props.backends[0]?.name || "", name: "", check: "", servers: ["", ""], err: "",
    settings: { ...backendData(USE_CASES[2]), balance: "roundrobin" } });
});
const guess = computed(() => (f.path.split("/").filter(Boolean).pop() || "service").replace(/[^A-Za-z0-9._-]/g, "-"));

function submit() {
  f.err = "";
  const fe = props.frontend!, path = f.path.trim().replace(/\/+$/, "") || "/";
  if (!path.startsWith("/") || /\s/.test(path)) { f.err = "The path must start with / and have no spaces, e.g. /api/payments"; return; }
  if (fe.routes.some((r) => (r.kind === "path_prefix" || r.kind === "path") && r.value === path)) { f.err = `${path} already has a route on ${fe.name}.`; return; }
  const ops: HxOp[] = [];
  let backend = f.existing;
  if (f.target === "new") {
    backend = f.name.trim() || guess.value;
    if (props.backends.some((b) => b.name === backend)) { f.err = `Backend ${backend} already exists: choose "Existing backend" or another name.`; return; }
    const servers = f.servers.map((x) => x.trim()).filter(Boolean);
    if (!servers.length) { f.err = "Add at least one server, e.g. 10.0.1.51:8080"; return; }
    const s: Settings = { ...f.settings, mode: "http", check: { type: "http", method: "GET", uri: f.check.trim() || "/health", host: "" } };
    ops.push({ op: "create", kind: "backend", name: backend, data: backendChanges(s, null) });
    for (const [i, x] of servers.entries()) {
      const hp = splitHostPort(x);
      if (!hp) { f.err = `Server ${i + 1}: write it as address:port, e.g. 10.0.1.51:8080`; return; }
      ops.push({ op: "create", kind: "server", backend, name: `${backend}-${i + 1}`, data: { ...hp, check: true } });
    }
  }
  // Keep the existing routes; put the new one before any shorter path that would also match it.
  const routes: { kind: string; value: string; backend: string; header?: string }[] = fe.routes.map((r) => ({ kind: r.kind, value: r.value, backend: r.backend, ...(r.header ? { header: r.header } : {}) }));
  const at = f.kind === "path_prefix" ? routes.findIndex((r) => r.kind === "path_prefix" && path.startsWith(r.value) && r.value.length < path.length) : -1;
  routes.splice(at < 0 ? routes.length : at, 0, { kind: f.kind, value: path, backend });
  ops.push({ op: "update", kind: "frontend", name: fe.name, data: { routes } });
  emit("review", ops, `Route ${path} → ${backend} on ${fe.name}`);
}
</script>

<template>
  <Modal :open="open && !!frontend" :title="`Add a route to ${frontend?.name}`" wide @close="emit('close')">
    <div class="hxr-row">
      <div class="form-row"><label class="lbl">Match</label><div class="segmented" role="group">
        <button type="button" :aria-pressed="f.kind === 'path_prefix'" @click="f.kind = 'path_prefix'">Path starts with</button>
        <button type="button" :aria-pressed="f.kind === 'path'" @click="f.kind = 'path'">Path is exactly</button></div></div>
      <div class="form-row" style="flex:1"><label class="lbl" for="hxr-path">Path</label>
        <input id="hxr-path" v-model="f.path" class="input mono" placeholder="/api/payments"></div>
    </div>
    <div class="form-row"><label class="lbl">Send it to</label><div class="segmented" role="group">
      <button type="button" :aria-pressed="f.target === 'new'" @click="f.target = 'new'">A new backend</button>
      <button type="button" :aria-pressed="f.target === 'existing'" :disabled="!backends.length" @click="f.target = 'existing'">An existing backend</button></div></div>

    <template v-if="f.target === 'new'">
      <div class="hxr-row">
        <div class="form-row"><label class="lbl" for="hxr-name">Backend name</label><input id="hxr-name" v-model="f.name" class="input mono" :placeholder="guess"></div>
        <div class="form-row"><label class="lbl" for="hxr-check">Health check path</label><input id="hxr-check" v-model="f.check" class="input mono" placeholder="/health"></div>
      </div>
      <div class="form-row"><label class="lbl">Servers</label>
        <div v-for="(_, i) in f.servers" :key="i" class="assign-row">
          <input v-model="f.servers[i]" class="input mono" :placeholder="`10.0.1.${51 + i}:8080`" :aria-label="`Server ${i + 1}`">
          <button class="btn icon-only ghost" type="button" aria-label="Remove server" :disabled="f.servers.length === 1" @click="f.servers.splice(i, 1)"><Icon name="x" /></button></div>
        <button class="btn sm" type="button" style="align-self:flex-start" @click="f.servers.push('')">Add server</button></div>
      <details class="hxr-more"><summary>Balancing, sessions and timeouts</summary><HxBackendSettings v-model="f.settings" hide-mode hide-check /></details>
    </template>
    <div v-else class="form-row"><label class="lbl" for="hxr-existing">Backend</label>
      <select id="hxr-existing" v-model="f.existing" class="select" style="max-width:280px"><option v-for="b in backends" :key="b.name" :value="b.name">{{ b.name }}</option></select></div>

    <p class="hint" style="margin:0">The route is placed before shorter paths that would also match, so <code>/api/payments</code> isn't caught by <code>/api</code>.</p>
    <div v-if="f.err" class="test-result show err"><Icon name="crit" /><span>{{ f.err }}</span></div>
    <template #actions><button class="btn ghost" type="button" @click="emit('close')">Cancel</button><span style="flex:1" />
      <button class="btn primary" type="button" @click="submit">Review change</button></template>
  </Modal>
</template>

<style scoped>
.mono { font-family: var(--mono); }
.hxr-row { display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-end; }
.hxr-row .form-row { min-width: 200px; flex: 1; }
.hxr-more { border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; }
.hxr-more summary { cursor: pointer; font-weight: 550; font-size: 13.5px; }
.hxr-more[open] summary { margin-bottom: 10px; }
</style>
