<script setup lang="ts">
// Are the HAProxy instances of a group configured the same? Shows what differs from this one and can
// copy this configuration to another member (reviewed and validated on that HAProxy before it applies).
import { computed, ref, watch } from "vue";
import Icon from "@/components/Icon.vue";
import Modal from "@/components/Modal.vue";
import { hx, type HxOp } from "@/lib/haproxy";

const props = defineProps<{ open: boolean; instanceId: number; instanceName: string; canConfigure: boolean }>();
const emit = defineEmits<{ close: []; review: [ops: HxOp[], title: string, target: { id: number; name: string }] }>();
type Drift = Awaited<ReturnType<typeof hx.groupDrift>>;
const d = ref<Drift | null>(null), err = ref(""), shown = ref(0), busy = ref(false);
async function load() {
  busy.value = true; err.value = "";
  try { d.value = await hx.groupDrift(props.instanceId); shown.value = Math.max(0, d.value.others.findIndex((o) => !o.same)); }
  catch (e: any) { err.value = e.message; } finally { busy.value = false; }
}
watch(() => props.open, (o) => { if (o) load(); });
const other = computed(() => d.value?.others[shown.value]);
const lines = computed(() => (other.value?.diff || "").split("\n").filter((l) => l && !l.startsWith("+++") && !l.startsWith("---")));
function copyTo() {
  const o = other.value;
  if (!o || !d.value) return;
  emit("review", [{ op: "raw", kind: "config", data: { text: o.text || d.value.text } }], `Make ${o.name} the same as ${props.instanceName}`, { id: o.id, name: o.name });
}
</script>

<template>
  <Modal :open="open" :title="`Group ${d?.group || ''}: same configuration?`" wide @close="emit('close')">
    <p v-if="busy" class="hint" style="margin:0">Comparing configurations…</p>
    <template v-else-if="d">
      <div class="hxd-members">
        <button v-for="(o, i) in d.others" :key="o.id" type="button" class="hxd-member" :aria-pressed="shown === i" @click="shown = i">
          <Icon :name="o.error ? 'crit' : o.same ? 'ok' : 'warn'" :style="{ color: o.error ? 'var(--critical)' : o.same ? 'var(--good)' : 'var(--warning)' }" />
          <b>{{ o.name }}</b><span class="hint">{{ o.error ? "unreachable" : o.same ? "same as this one" : "differs" }}</span></button></div>
      <template v-if="other">
        <div v-if="other.error" class="test-result show err"><Icon name="crit" /><span>{{ other.error }}</span></div>
        <div v-else-if="other.same" class="test-result show ok"><Icon name="ok" /><span>{{ other.name }} has exactly the same configuration as {{ instanceName }}.</span></div>
        <template v-else>
          <p class="hint" style="margin:0">Lines with <b>+</b> are in {{ instanceName }} but not in {{ other.name }}; lines with <b>−</b> are only in {{ other.name }}. Each HAProxy keeps its own <code>log-tag</code>.</p>
          <div class="ye-diff hx-diff" role="region" aria-label="Configuration difference">
            <div v-for="(l, i) in lines" :key="i" class="ye-line hx-line" :class="{ add: l[0] === '+', del: l[0] === '-', hunk: l.startsWith('@@') }">
              <span class="ye-op">{{ l.startsWith("@@") ? "" : l[0] === "-" ? "−" : l[0] === "+" ? "+" : "" }}</span><span class="ye-t">{{ l.startsWith("@@") ? "⋯" : l.slice(1) }}</span></div>
          </div>
        </template>
      </template>
    </template>
    <div v-if="err" class="test-result show err"><Icon name="crit" /><span>{{ err }}</span></div>
    <template #actions><button class="btn ghost" type="button" @click="emit('close')">Close</button><button class="btn ghost" type="button" :disabled="busy" @click="load">Compare again</button><span style="flex:1" />
      <button v-if="canConfigure && other && !other.same && !other.error" class="btn primary" type="button" @click="copyTo">Make {{ other.name }} the same as {{ instanceName }}</button></template>
  </Modal>
</template>

<style scoped>
.hxd-members { display: flex; gap: 8px; flex-wrap: wrap; }
.hxd-member { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border: 1px solid var(--border); border-radius: 10px; background: none; color: var(--text); font: inherit; cursor: pointer; }
.hxd-member[aria-pressed="true"] { border-color: var(--accent); background: var(--surface-active); }
.hxd-member .icon { width: 16px; height: 16px; }
</style>
