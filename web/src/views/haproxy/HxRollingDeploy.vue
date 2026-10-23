<script setup lang="ts">
// Guided rolling deploy: one server at a time — drain, wait for its sessions to end, deploy, put back, wait healthy.
import { computed, onBeforeUnmount, ref, watch } from "vue";
import Badge from "@/components/Badge.vue";
import Icon from "@/components/Icon.vue";
import Modal from "@/components/Modal.vue";
import { hx, STATE, type HxBackend } from "@/lib/haproxy";
import { useHaproxy } from "@/stores/haproxy";
import { toast } from "@/stores/ui";

const props = defineProps<{ open: boolean; backend: HxBackend | null }>();
const emit = defineEmits<{ close: [] }>();
const h = useHaproxy();
type Step = "pick" | "draining" | "deploy" | "returning" | "done";
const order = ref<string[]>([]), i = ref(0), step = ref<Step>("pick"), busy = ref(false), err = ref(""), waited = ref(0);
let timer = 0;
watch(() => props.open, (o) => {
  clearInterval(timer);
  if (!o || !props.backend) return;
  order.value = props.backend.servers.filter((s) => !s.backup).map((s) => s.name);
  i.value = 0; step.value = "pick"; err.value = ""; waited.value = 0;
  timer = window.setInterval(async () => { if (step.value === "draining" || step.value === "returning") { waited.value += 2; await h.refresh(); } }, 2000);
});
onBeforeUnmount(() => clearInterval(timer));
const live = computed(() => h.data?.backends.find((b) => b.name === props.backend?.name));
const server = computed(() => live.value?.servers.find((s) => s.name === order.value[i.value]));
const sessions = computed(() => Number(server.value?.stats.scur) || 0);
const othersUp = computed(() => (live.value?.servers || []).filter((s) => s.name !== server.value?.name && (s.state === "up" || s.state === "nocheck")).length);
async function setState(state: "drain" | "ready") {
  busy.value = true; err.value = "";
  try { await hx.setState(h.currentId!, props.backend!.name, order.value[i.value], state); await h.refresh(); waited.value = 0; }
  catch (e: any) { err.value = e.message; throw e; } finally { busy.value = false; }
}
async function start() { step.value = "draining"; try { await setState("drain"); } catch { step.value = "pick"; } }
async function back() { step.value = "returning"; try { await setState("ready"); } catch { step.value = "deploy"; } }
watch([sessions, step, () => server.value?.state], () => {
  if (step.value === "draining" && server.value && sessions.value === 0 && server.value.state === "drain") step.value = "deploy";
});
// Watch the state together with the step: the server can come back up before the step changes.
watch([() => server.value?.state, step], ([st]) => {
  if (step.value === "returning" && (st === "up" || st === "nocheck")) {
    if (i.value + 1 < order.value.length) { i.value++; step.value = "pick"; }
    else { step.value = "done"; toast(true, `Rolling deploy of ${props.backend?.name} finished`); }
  }
});
function move(k: number, d: number) { const o = order.value; [o[k], o[k + d]] = [o[k + d], o[k]]; }
</script>
<template>
  <Modal :open="open" :title="`Rolling deploy: ${backend?.name}`" wide @close="emit('close')">
    <p class="hint" style="margin:0">Update the servers one at a time while the others keep serving. Each server is drained (no new connections), you deploy once its sessions reach zero, then it's put back and must pass its health check before the next one starts.</p>
    <ol class="hx-steps">
      <li v-for="(name, k) in order" :key="name" :class="{ now: k === i && step !== 'done', done: k < i || step === 'done' }">
        <span class="n">{{ k < i || step === "done" ? "✓" : k + 1 }}</span><span class="mono" style="flex:1">{{ name }}</span>
        <Badge v-if="live" :level="STATE[live.servers.find((s) => s.name === name)?.state || 'unknown'].level" :label="STATE[live.servers.find((s) => s.name === name)?.state || 'unknown'].label" />
        <template v-if="step === 'pick' && k >= i"><button class="btn icon-only ghost" :disabled="k === i" aria-label="Move up" @click="move(k, -1)">↑</button>
          <button class="btn icon-only ghost" :disabled="k === order.length - 1" aria-label="Move down" @click="move(k, 1)">↓</button></template>
      </li></ol>
    <div v-if="step !== 'done' && server" class="card" style="padding:14px 16px;display:flex;flex-direction:column;gap:8px">
      <b>Server {{ i + 1 }} of {{ order.length }}: <span class="mono">{{ server.name }}</span> ({{ server.address }}:{{ server.port }})</b>
      <template v-if="step === 'pick'">
        <span class="hint">{{ othersUp }} other server(s) are up and will take the traffic.</span>
        <div v-if="othersUp === 0" class="test-result show err"><Icon name="crit" /><span>No other server is up: draining this one would take the whole backend offline.</span></div>
        <div><button class="btn primary" :disabled="busy || othersUp === 0" @click="start">Drain {{ server.name }}</button></div></template>
      <template v-else-if="step === 'draining'">
        <span>Waiting for open sessions to finish: <b>{{ sessions }}</b> left · {{ waited }} s</span>
        <div><button class="btn" @click="step = 'deploy'">Continue anyway</button></div></template>
      <template v-else-if="step === 'deploy'">
        <div class="test-result show ok"><Icon name="ok" /><span><b>{{ server.name }}</b> has no traffic. Deploy your new version to it now.</span></div>
        <div><button class="btn primary" :disabled="busy" @click="back">Deployed, put it back</button></div></template>
      <template v-else-if="step === 'returning'">
        <span>Waiting for the health check to pass: <b>{{ STATE[server.state].label }}</b> · {{ waited }} s</span>
        <span v-if="waited > 30" class="hint" style="color:var(--critical)">Still not healthy. Check the deploy on {{ server.name }}; the other servers keep serving.</span></template>
    </div>
    <div v-if="step === 'done'" class="test-result show ok"><Icon name="ok" /><span>All {{ order.length }} servers were updated and are back in service.</span></div>
    <div v-if="err" class="test-result show err"><Icon name="crit" /><span>{{ err }}</span></div>
    <template #actions><span style="flex:1" /><button class="btn" type="button" @click="emit('close')">{{ step === "done" ? "Close" : "Stop here" }}</button></template>
  </Modal>
</template>
