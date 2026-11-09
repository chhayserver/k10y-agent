<script setup lang="ts">
// An upstream: how it balances and its servers (address, weight, backup, down, failure limits).
import { computed, reactive, watch } from "vue";
import Icon from "@/components/Icon.vue";
import Modal from "@/components/Modal.vue";
import type { NgUpstream } from "@/lib/nginx";

const props = defineProps<{ open: boolean; upstream?: NgUpstream | null }>();
const emit = defineEmits<{ close: []; submit: [data: any] }>();
const METHODS = [["round_robin", "Round robin", "Each server in turn, by weight. The usual choice."],
  ["least_conn", "Least connections", "The server with the fewest open requests: good when requests take different times."],
  ["ip_hash", "Client IP", "The same client always reaches the same server (simple stickiness)."],
  ["hash", "Hash", "By a key you choose, e.g. $request_uri for caches."],
  ["random", "Random", "A random server (two picks, the less busy one, with \"random two\")."]];
type Row = { address: string; weight: number; max_fails: string; fail_timeout: string; backup: boolean; down: boolean };
const f = reactive({ name: "", method: "round_robin", hash_key: "$request_uri", keepalive: "", servers: [] as Row[] });
watch(() => props.open, (o) => {
  if (!o) return;
  const u = props.upstream;
  if (!u) { Object.assign(f, { name: "", method: "round_robin", hash_key: "$request_uri", keepalive: "16", servers: [
    { address: "10.0.0.11:8080", weight: 1, max_fails: "", fail_timeout: "", backup: false, down: false },
    { address: "10.0.0.12:8080", weight: 1, max_fails: "", fail_timeout: "", backup: false, down: false }] }); return; }
  Object.assign(f, { name: u.name, method: ["round_robin", "least_conn", "ip_hash", "hash", "random"].includes(u.method) ? u.method : "round_robin",
    hash_key: u.method === "hash" ? u.method_args.split(" ")[0] : "$request_uri", keepalive: u.keepalive || "",
    servers: u.servers.map((s) => ({ address: s.address, weight: s.weight, max_fails: s.max_fails || "", fail_timeout: s.fail_timeout || "", backup: s.backup, down: s.down })) });
}, { immediate: true });
const lost = computed(() => props.upstream?.other || []);
const valid = computed(() => /^[A-Za-z0-9._-]+$/.test(f.name) && f.servers.some((s) => s.address.trim()));
function submit() {
  emit("submit", { name: f.name, method: f.method, hash_key: f.hash_key, keepalive: f.keepalive ? Number(f.keepalive) : undefined,
    servers: f.servers.filter((s) => s.address.trim()).map((s) => ({ ...s, address: s.address.trim(), weight: Number(s.weight) || 1 })) });
}
</script>

<template>
  <Modal :open="open" :title="upstream ? `Edit upstream ${upstream.name}` : 'Add an upstream'" wide @close="emit('close')">
    <div class="form-row"><label class="lbl" for="up-n">Name</label><input id="up-n" v-model="f.name" class="input mono" :disabled="!!upstream" placeholder="shop_backend">
      <span class="hint">Routes send traffic to it with <code>proxy_pass http://{{ f.name || "name" }};</code></span></div>
    <div class="form-row"><label class="lbl" for="up-m">Balancing</label><div class="field">
      <select id="up-m" v-model="f.method" class="select" style="max-width:260px"><option v-for="[v, l] in METHODS" :key="v" :value="v">{{ l }}</option></select>
      <input v-if="f.method === 'hash'" v-model="f.hash_key" class="input mono" aria-label="Hash key"></div>
      <span class="hint">{{ METHODS.find((m) => m[0] === f.method)?.[2] }}</span></div>
    <div class="form-row"><span class="lbl">Servers</span>
      <table class="data up-form"><thead><tr><th>Address</th><th class="num">Weight</th><th>Max fails</th><th>Fail timeout</th><th>Backup</th><th>Down</th><th /></tr></thead>
        <tbody><tr v-for="(s, i) in f.servers" :key="i">
          <td><input v-model="s.address" class="input mono" placeholder="10.0.0.11:8080" aria-label="Address"></td>
          <td><input v-model.number="s.weight" class="input" type="number" min="1" max="100" style="width:70px" aria-label="Weight"></td>
          <td><input v-model="s.max_fails" class="input" style="width:70px" placeholder="1" aria-label="Max fails"></td>
          <td><input v-model="s.fail_timeout" class="input" style="width:80px" placeholder="10s" aria-label="Fail timeout"></td>
          <td><input v-model="s.backup" type="checkbox" aria-label="Backup"></td><td><input v-model="s.down" type="checkbox" aria-label="Down"></td>
          <td><button class="btn ghost sm" type="button" aria-label="Remove server" @click="f.servers.splice(i, 1)">×</button></td></tr></tbody></table>
      <button class="btn sm" type="button" style="align-self:flex-start" @click="f.servers.push({ address: '', weight: 1, max_fails: '', fail_timeout: '', backup: false, down: false })">Add server</button>
      <span class="hint"><b>Backup</b> gets traffic only when the others fail. <b>Down</b> keeps a server in the list but sends it nothing.</span></div>
    <div class="form-row"><label class="lbl" for="up-k">Keepalive connections (optional)</label><input id="up-k" v-model="f.keepalive" class="input" type="number" min="0" style="max-width:120px" placeholder="16">
      <span class="hint">Idle connections kept open to the servers; faster for busy APIs.</span></div>
    <div v-if="lost.length" class="test-result show err"><Icon name="warn" /><span>This upstream also has <code>{{ lost.join(", ") }}</code>, which this form doesn't manage: saving removes them. To keep them, edit the file in Raw config instead.</span></div>
    <template #actions><button class="btn ghost" type="button" @click="emit('close')">Cancel</button><span style="flex:1" />
      <button class="btn primary" type="button" :disabled="!valid" @click="submit">Review</button></template>
  </Modal>
</template>

<style scoped>
.up-form td { padding: 4px 6px; }
.up-form .input { height: 32px; }
</style>
