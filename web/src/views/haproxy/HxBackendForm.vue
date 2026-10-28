<script setup lang="ts">
// Create or edit a backend: pick what it is for (web app, API, database…) to start from sensible settings,
// then balancing, health check, sessions and timeouts. New backends get their first servers here.
import { ref, watch } from "vue";
import Icon from "@/components/Icon.vue";
import Modal from "@/components/Modal.vue";
import type { HxBackend, HxOp } from "@/lib/haproxy";
import { USE_CASES, backendChanges, backendData, serverName, settingsOf, splitHostPort, timingData, useCase, type Settings } from "@/lib/haproxyCatalog";
import HxBackendSettings from "./HxBackendSettings.vue";

const props = defineProps<{ open: boolean; backend: HxBackend | null }>();
const emit = defineEmits<{ close: []; review: [ops: HxOp[], title: string] }>();
const name = ref(""), err = ref(""), preset = ref("api");
const settings = ref<Settings>(backendData(useCase("api")!));
const servers = ref<{ address: string; backup: boolean }[]>([]);

function start(id: string) {
  const u = useCase(id)!;
  preset.value = id;
  settings.value = backendData(u, { path: u.backends[0].check || undefined });
  servers.value = u.backends[u.backends.length - 1].servers.map((x) => ({ address: "", backup: !!x.backup }));
  if (!name.value || USE_CASES.some((c) => c.backends.some((b) => b.name === name.value))) name.value = u.backends[u.backends.length - 1].name;
}
const srvName = (i: number) => serverName(useCase(preset.value)?.serverName, (name.value || "backend").trim(), i);
const example = (i: number) => useCase(preset.value)?.backends.at(-1)?.servers[i]?.address || (settings.value.mode === "http" ? "10.0.1.10:8080" : "10.0.3.10:5432");

watch(() => props.open, (o) => {
  if (!o) return;
  err.value = "";
  const b = props.backend;
  if (b) { name.value = b.name; settings.value = settingsOf(b); servers.value = []; }
  else { name.value = ""; start("api"); }
});

function submit() {
  err.value = "";
  const n = name.value.trim();
  if (!n) { err.value = "Give the backend a name, e.g. users or postgres."; return; }
  const s = settings.value;
  if (["hdr", "url_param", "hash"].includes(s.balance) && !s.balance_arg.trim()) { err.value = "This balancing method needs a value (see the field next to it)."; return; }
  const data = backendChanges(s, props.backend ? settingsOf(props.backend) : null);
  if (props.backend && !Object.keys(data).length) { err.value = "Nothing changed."; return; }
  const ops: HxOp[] = [{ op: props.backend ? "update" : "create", kind: "backend", name: n, data }];
  const rows = servers.value.filter((x) => x.address.trim());
  for (const [i, row] of rows.entries()) {
    const hp = splitHostPort(row.address);
    if (!hp) { err.value = `Server ${i + 1}: write it as address:port, e.g. ${example(i)}`; return; }
    ops.push({ op: "create", kind: "server", backend: n, name: srvName(i), data: { ...hp, check: true, backup: row.backup, ...timingData(s) } });
  }
  emit("review", ops, props.backend ? `Edit backend ${n}` : `New backend ${n}`);
}
</script>

<template>
  <Modal :open="open" :title="backend ? `Edit backend ${backend.name}` : 'New backend'" wide @close="emit('close')">
    <p class="hint" style="margin:0">A backend is a group of servers that share the traffic, with a balancing method and a health check.</p>

    <div v-if="!backend" class="form-row"><label class="lbl">What is it for?</label>
      <div class="hxbf-presets">
        <button v-for="u in USE_CASES.filter((x) => x.id !== 'microservices')" :key="u.id" type="button" class="hxbf-preset" :aria-pressed="preset === u.id" @click="start(u.id)">
          <Icon :name="u.icon" /><span>{{ u.label }}</span></button></div>
      <span class="hint">{{ useCase(preset)?.desc }} You can change every setting below.</span></div>

    <div class="form-row"><label class="lbl" for="hxbf-name">Name</label>
      <input id="hxbf-name" v-model="name" class="input" :disabled="!!backend" placeholder="e.g. users, orders, postgres" style="font-family:var(--mono);max-width:320px"></div>

    <HxBackendSettings v-model="settings" />

    <div v-if="!backend" class="form-row"><label class="lbl">Servers</label>
      <div v-for="(row, i) in servers" :key="i" class="assign-row">
        <span class="hint mono" style="min-width:110px">{{ srvName(i) }}</span>
        <input v-model="row.address" class="input" :placeholder="example(i)" style="font-family:var(--mono)" :aria-label="`Server ${i + 1} address and port`">
        <label class="pill"><input v-model="row.backup" type="checkbox"> Backup</label>
        <button class="btn icon-only ghost" type="button" aria-label="Remove server" @click="servers.splice(i, 1)"><Icon name="x" /></button></div>
      <button class="btn sm" type="button" style="align-self:flex-start" @click="servers.push({ address: '', backup: false })">Add server</button>
      <span class="hint">Address and port, e.g. <code>10.0.1.11:8080</code> or <code>users.internal:8080</code>. A backup server only gets traffic when all the others are down.</span></div>

    <div v-if="err" class="test-result show err"><Icon name="crit" /><span>{{ err }}</span></div>
    <template #actions><button class="btn ghost" type="button" @click="emit('close')">Cancel</button><span style="flex:1" />
      <button class="btn primary" type="button" @click="submit">Review change</button></template>
  </Modal>
</template>

<style scoped>
.mono { font-family: var(--mono); }
.hxbf-presets { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px; }
.hxbf-preset { display: flex; align-items: center; gap: 8px; padding: 9px 11px; border: 1px solid var(--border); border-radius: 10px; background: none; color: var(--text); font: inherit; font-size: 13px; cursor: pointer; text-align: left; }
.hxbf-preset:hover { background: var(--surface-hover); }
.hxbf-preset[aria-pressed="true"] { border-color: var(--accent); background: var(--surface-active); }
.hxbf-preset .icon { width: 16px; height: 16px; color: var(--muted); flex: none; }
</style>
