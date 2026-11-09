<script setup lang="ts">
// The configuration as Nginx reads it (`nginx -T`: every included file), and an editor for one file at a time
// whose changes go through the same review (diff, nginx -t, reload).
import { computed, onMounted, reactive, ref } from "vue";
import Icon from "@/components/Icon.vue";
import { ng, type NgOp } from "@/lib/nginx";
import { useNginx } from "@/stores/nginx";
import { useSession } from "@/stores/session";
import { toast } from "@/stores/ui";
import NgChangeDialog from "./NgChangeDialog.vue";

const n = useNginx(), s = useSession();
const canEdit = computed(() => s.canNg("nginx.configure", n.currentId));
const text = ref(""), check = ref(""), error = ref(""), loading = ref(true);
const files = computed(() => n.data?.config.files || []);
const e = reactive({ on: false, path: "", original: "", text: "", loading: false });
const review = reactive({ open: false, ops: [] as NgOp[] });
async function load() {
  loading.value = true;
  try { const r = await ng.raw(n.currentId!); text.value = r.text; check.value = r.check; error.value = ""; } catch (err: any) { error.value = err.message; } finally { loading.value = false; }
}
onMounted(load);
async function edit(path: string) {
  e.loading = true;
  try { const r = await ng.file(n.currentId!, path); Object.assign(e, { on: true, path, original: r.text, text: r.text }); }
  catch (err: any) { toast(false, err.message); } finally { e.loading = false; }
}
function onKey(ev: KeyboardEvent) {
  if (ev.key !== "Tab" || ev.shiftKey) return;
  ev.preventDefault();
  const el = ev.target as HTMLTextAreaElement, a = el.selectionStart;
  e.text = e.text.slice(0, a) + "    " + e.text.slice(el.selectionEnd);
  requestAnimationFrame(() => el.setSelectionRange(a + 4, a + 4));
}
function done() { review.open = false; e.on = false; load(); }
async function copy() { try { await navigator.clipboard.writeText(text.value); toast(true, "Copied"); } catch { toast(false, "Copy failed"); } }
</script>

<template>
  <div class="toolbar">
    <template v-if="!e.on">
      <span class="hint" style="flex:1">Every file Nginx loads, in order (<code>nginx -T</code>).</span>
      <select v-if="canEdit" class="select" style="max-width:340px" aria-label="Edit a file" :disabled="e.loading" @change="edit(($event.target as HTMLSelectElement).value); ($event.target as HTMLSelectElement).value = ''">
        <option value="">Edit a file…</option><option v-for="f in files" :key="f" :value="f">{{ f }}</option></select>
      <button class="btn" type="button" :disabled="!text" @click="copy"><Icon name="copy" /><span>Copy</span></button>
    </template>
    <template v-else>
      <span class="mono" style="flex:1">{{ e.path }}</span>
      <button class="btn ghost" type="button" @click="e.on = false">Cancel</button>
      <button class="btn primary" type="button" :disabled="e.text === e.original" @click="Object.assign(review, { open: true, ops: [{ kind: 'file', op: 'update', path: e.path, text: e.text }] })">Review</button>
    </template>
  </div>
  <div v-if="error" class="banner show"><Icon name="crit" /><span class="msg">{{ error }}</span></div>
  <div v-if="n.data?.config.errors.length" class="banner show"><Icon name="warn" /><span class="msg">
    <span v-for="(x, i) in n.data.config.errors" :key="i" style="display:block">{{ x.file }}:{{ x.line }}: {{ x.message }}</span></span></div>
  <textarea v-if="e.on" v-model="e.text" class="ye-text ng-editor" spellcheck="false" autocapitalize="off" :aria-label="e.path" @keydown="onKey" />
  <template v-else>
    <p v-if="check" class="hint mono">{{ check }}</p>
    <pre class="logs" style="max-height:none;white-space:pre">{{ loading ? "Loading…" : text }}</pre>
  </template>
  <NgChangeDialog :open="review.open" :title="`Edit ${e.path.split('/').pop()}`" :ops="review.ops" @close="review.open = false" @done="done" />
</template>

<style scoped>
.ng-editor { min-height: 60vh; width: 100%; }
</style>
