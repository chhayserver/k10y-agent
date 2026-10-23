<script setup lang="ts">
import { reactive, watch } from "vue";
import { hub } from "@/api/client";
import Icon from "@/components/Icon.vue";
import Modal from "@/components/Modal.vue";
import PasswordInput from "@/components/PasswordInput.vue";
import { toast } from "@/stores/ui";

const props = defineProps<{ open: boolean; instance: any | null }>();
const emit = defineEmits<{ close: []; saved: [id: number] }>();
const f = reactive({ name: "", url: "http://", username: "admin", password: "", verify_tls: false, test: null as null | { ok: boolean; text: string }, busy: false });
watch(() => props.open, (o) => {
  if (!o) return;
  const i = props.instance;
  Object.assign(f, { name: i?.name || "", url: i?.url || "http://", username: i?.username || "admin", password: "", verify_tls: i?.verify_tls || false, test: null, busy: false });
});
async function test() {
  f.busy = true; f.test = null;
  try {
    const r = await hub.post("/haproxy/test", { url: f.url, username: f.username, password: f.password || null, verify_tls: f.verify_tls, instance_id: props.instance?.id || null });
    f.test = { ok: true, text: `Connected · Data Plane API ${r.api_version} · ${r.frontends} services, ${r.backends} backends, ${r.servers} servers` };
    return true;
  } catch (e: any) { f.test = { ok: false, text: e.message }; return false; } finally { f.busy = false; }
}
async function save() {
  if (!(await test())) return;
  f.busy = true;
  try {
    const body = { name: f.name, url: f.url, username: f.username, password: f.password || null, verify_tls: f.verify_tls };
    const r = props.instance ? await hub.patch(`/haproxy/${props.instance.id}`, body) : await hub.post("/haproxy", body);
    toast(true, `Saved ${r.name}`); emit("saved", r.id);
  } catch (e: any) { f.test = { ok: false, text: e.message }; } finally { f.busy = false; }
}
</script>
<template>
  <Modal :open="open" :title="instance ? `Edit ${instance.name}` : 'Add HAProxy'" @close="emit('close')">
    <p class="hint" style="margin:0">k10y manages HAProxy through its <b>Data Plane API</b> (v3, included in the official HAProxy images).</p>
    <div class="form-row"><label class="lbl">Name</label><input v-model="f.name" class="input" placeholder="e.g. edge-lb-1"></div>
    <div class="form-row"><label class="lbl">Data Plane API URL</label><input v-model="f.url" class="input" placeholder="http://10.0.0.20:5555" style="font-family:var(--mono)"></div>
    <div class="two-col" style="margin:0">
      <div class="form-row"><label class="lbl">Username</label><input v-model="f.username" class="input" autocomplete="off"></div>
      <div class="form-row"><label class="lbl">Password {{ instance ? "(leave empty to keep)" : "" }}</label><PasswordInput v-model="f.password" autocomplete="off" /></div>
    </div>
    <label class="pill" style="align-self:flex-start"><input v-model="f.verify_tls" type="checkbox"> Verify TLS certificate (for https URLs)</label>
    <div v-if="f.test" class="test-result show" :class="f.test.ok ? 'ok' : 'err'"><Icon :name="f.test.ok ? 'ok' : 'crit'" /><span>{{ f.test.text }}</span></div>
    <template #actions><button class="btn ghost" type="button" @click="emit('close')">Cancel</button><span style="flex:1" />
      <button class="btn" type="button" :disabled="f.busy" @click="test">Test connection</button>
      <button class="btn primary" type="button" :disabled="f.busy || !f.name || !f.url || (!instance && !f.password)" @click="save">{{ instance ? "Save" : "Add HAProxy" }}</button></template>
  </Modal>
</template>
