<script setup lang="ts">
import { reactive, watch } from "vue";
import Icon from "@/components/Icon.vue";
import Modal from "@/components/Modal.vue";
import type { HxOp, HxServer } from "@/lib/haproxy";

const props = defineProps<{ open: boolean; backend: string; server: HxServer | null }>();
const emit = defineEmits<{ close: []; review: [ops: HxOp[], title: string] }>();
const f = reactive({ name: "", address: "", port: 80 as number | string, weight: 100 as number | string, check: true, backup: false, err: "" });
watch(() => props.open, (o) => {
  if (!o) return;
  const s = props.server;
  Object.assign(f, { name: s?.name || "", address: s?.address || "", port: s?.port || 80, weight: s?.weight ?? 100, check: s ? s.check : true, backup: s?.backup || false, err: "" });
});
function submit() {
  f.err = "";
  if (!f.name.trim() || !f.address.trim()) { f.err = "Name and address are required."; return; }
  emit("review", [{ op: props.server ? "update" : "create", kind: "server", backend: props.backend, name: f.name.trim(),
    data: { address: f.address.trim(), port: Number(f.port), weight: Number(f.weight), check: f.check, backup: f.backup } }],
  props.server ? `Edit server ${props.backend}/${f.name}` : `Add server to ${props.backend}`);
}
</script>
<template>
  <Modal :open="open" :title="server ? `Edit server ${backend}/${server.name}` : `Add server to ${backend}`" @close="emit('close')">
    <div class="form-row"><label class="lbl">Name</label><input v-model="f.name" class="input" :disabled="!!server" placeholder="e.g. web3" style="font-family:var(--mono)"></div>
    <div class="two-col" style="margin:0">
      <div class="form-row"><label class="lbl">Address</label><input v-model="f.address" class="input" placeholder="10.0.0.12 or web3.internal" style="font-family:var(--mono)"></div>
      <div class="form-row"><label class="lbl">Port</label><input v-model="f.port" class="input" type="number" min="1" max="65535"></div>
    </div>
    <div class="form-row"><label class="lbl">Weight</label><input v-model="f.weight" class="input" type="number" min="0" max="256">
      <span class="hint">Share of traffic relative to the other servers (0–256). 0 sends no new traffic.</span></div>
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <label class="pill"><input v-model="f.check" type="checkbox"> Health check</label>
      <label class="pill"><input v-model="f.backup" type="checkbox"> Backup (only used when all others are down)</label></div>
    <p class="hint" style="margin:0">To take a server out of rotation temporarily, use <b>Drain</b> or <b>Maintenance</b> in its menu instead: that's instant and needs no reload.</p>
    <div v-if="f.err" class="test-result show err"><Icon name="crit" /><span>{{ f.err }}</span></div>
    <template #actions><button class="btn ghost" type="button" @click="emit('close')">Cancel</button><span style="flex:1" />
      <button class="btn primary" type="button" @click="submit">Review change</button></template>
  </Modal>
</template>
