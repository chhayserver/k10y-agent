<script setup lang="ts">
// The full haproxy.cfg, editable for anything the forms don't cover. Same review flow: diff, validate, apply.
import { onMounted, ref } from "vue";
import Icon from "@/components/Icon.vue";
import { hx, type HxOp } from "@/lib/haproxy";

const props = defineProps<{ instanceId: number; canEdit: boolean }>();
const emit = defineEmits<{ review: [ops: HxOp[], title: string] }>();
const original = ref(""), text = ref(""), error = ref(""), loading = ref(true), editing = ref(false);
async function load() {
  loading.value = true; error.value = "";
  try { const r = await hx.config(props.instanceId); original.value = text.value = r.text; editing.value = false; }
  catch (e: any) { error.value = e.message; } finally { loading.value = false; }
}
onMounted(load);
defineExpose({ load });
function onKey(e: KeyboardEvent) {
  if (e.key !== "Tab" || e.shiftKey) return;
  e.preventDefault();
  const el = e.target as HTMLTextAreaElement, a = el.selectionStart;
  text.value = text.value.slice(0, a) + "  " + text.value.slice(el.selectionEnd);
  requestAnimationFrame(() => el.setSelectionRange(a + 2, a + 2));
}
</script>
<template>
  <div class="yaml-editor">
    <div class="ye-bar">
      <span class="hint" style="flex:1">The live <code>haproxy.cfg</code>, as the Data Plane API manages it. Changes are validated by HAProxy and shown as a diff before they apply.</span>
      <button class="btn" :disabled="loading" @click="load"><Icon name="refresh" /><span>Reload</span></button>
      <template v-if="canEdit">
        <button v-if="!editing" class="btn primary" :disabled="loading || !!error" @click="editing = true"><Icon name="file" /><span>Edit</span></button>
        <template v-else><button class="btn ghost" @click="text = original; editing = false">Cancel</button>
          <button class="btn primary" :disabled="text === original" @click="emit('review', [{ op: 'raw', kind: 'config', data: { text } }], 'Edit HAProxy configuration')">Review change</button></template>
      </template>
    </div>
    <div v-if="error" class="test-result show err"><Icon name="crit" /><span>{{ error }}</span></div>
    <textarea v-if="editing" v-model="text" class="ye-text" spellcheck="false" autocapitalize="off" aria-label="haproxy.cfg" @keydown="onKey" />
    <pre v-else class="logs" style="max-height:none;white-space:pre">{{ loading ? "Loading…" : original }}</pre>
  </div>
</template>
