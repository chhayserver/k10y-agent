<script setup lang="ts">
// Every Nginx change goes through here: a dry run first (the exact diff and nginx -t's verdict, with each error's
// file, line and text), then Apply, which writes the files, checks again and reloads Nginx.
import { computed, ref, watch } from "vue";
import Icon from "@/components/Icon.vue";
import Modal from "@/components/Modal.vue";
import { ng, type NgOp, type NgResult } from "@/lib/nginx";
import { useNginx } from "@/stores/nginx";
import { toast } from "@/stores/ui";

const props = defineProps<{ open: boolean; title: string; ops?: NgOp[]; restore?: number; danger?: boolean }>();
const emit = defineEmits<{ close: []; done: [] }>();
const n = useNginx();
const plan = ref<NgResult | null>(null), busy = ref(false), error = ref("");
async function run(apply: boolean) {
  busy.value = true; error.value = "";
  try {
    const r = props.restore != null ? await ng.restore(n.currentId!, props.restore, apply) : await ng.change(n.currentId!, props.ops || [], apply, props.title);
    plan.value = r;
    if (apply && r.applied) { toast(true, `Applied: ${r.summary.join("; ")}`); emit("done"); n.refresh(); }
  } catch (e: any) { error.value = e.message; plan.value = null; } finally { busy.value = false; }
}
watch(() => props.open, (o) => { if (o) { plan.value = null; run(false); } }, { immediate: true });
const lines = computed(() => (plan.value?.diff || "").split("\n").filter((l) => !l.startsWith("+++") && l !== ""));
const short = (p: string) => p.split("/").slice(-2).join("/");
</script>

<template>
  <Modal :open="open" :title="title" wide @close="emit('close')">
    <div v-if="busy && !plan" class="hint">Checking the change with <code>nginx -t</code>…</div>
    <div v-if="error" class="test-result show err"><Icon name="crit" /><span>{{ error }}</span></div>
    <template v-if="plan">
      <ul class="hx-summary"><li v-for="x in plan.summary" :key="x">{{ x }}</li></ul>
      <div class="test-result show" :class="plan.valid ? 'ok' : 'err'"><Icon :name="plan.valid ? 'ok' : 'crit'" />
        <span>{{ plan.applied ? "Applied and reloaded." : plan.valid ? "nginx -t accepts the new configuration. Nothing has changed yet."
          : `nginx -t refuses it (${plan.errors.length || 1} problem${plan.errors.length > 1 ? "s" : ""}). Nothing was changed.` }}</span></div>
      <div v-if="!plan.valid" class="hx-cfgerr" role="list" aria-label="Configuration problems">
        <div v-for="(e, i) in plan.errors" :key="i" class="hx-cfgerr-row" role="listitem">
          <span class="hx-cfgerr-where">{{ short(e.file) }}:{{ e.line }}</span>
          <code v-if="e.text" class="hx-cfgerr-code">{{ e.text }}</code><span class="hx-cfgerr-msg">{{ e.message }}</span></div>
        <pre v-if="!plan.errors.length" class="logs" style="margin:0">{{ plan.check }}</pre>
      </div>
      <div class="lbl" style="margin-top:4px">Changes</div>
      <div class="ye-diff hx-diff" role="region" aria-label="Configuration diff">
        <div v-if="!lines.length" class="ye-gap" style="padding-left:12px">No changes</div>
        <div v-for="(l, i) in lines" :key="i" class="ye-line hx-line" :class="{ add: l[0] === '+', del: l[0] === '-', hunk: l.startsWith('@@'), file: l.startsWith('---') }">
          <span class="ye-op">{{ l.startsWith("@@") || l.startsWith("---") ? "" : l[0] === "-" ? "−" : l[0] === "+" ? "+" : "" }}</span>
          <span class="ye-t">{{ l.startsWith("---") ? l.slice(4) : l.startsWith("@@") ? "⋯" : l.slice(1) }}</span></div>
      </div>
      <p class="hint" style="margin:0">Applying writes these files, checks them again with <code>nginx -t</code> and reloads Nginx gracefully:
        open connections finish, new ones use the new configuration. If anything fails, the old files are put back.</p>
    </template>
    <template #actions><button class="btn ghost" type="button" @click="emit('close')">{{ plan?.applied ? "Close" : "Cancel" }}</button><span style="flex:1" />
      <button v-if="!plan?.applied" class="btn" type="button" :disabled="busy" @click="run(false)">Check again</button>
      <button v-if="!plan?.applied" class="btn" :class="danger ? 'danger' : 'primary'" type="button" :disabled="busy || !plan?.valid" @click="run(true)">
        {{ busy && plan ? "Applying…" : "Apply" }}</button></template>
  </Modal>
</template>

<style scoped>
.hx-cfgerr { display: flex; flex-direction: column; gap: 8px; padding: 10px 12px; border: 1px solid var(--critical); border-radius: 10px; }
.hx-cfgerr-row { display: grid; grid-template-columns: 180px minmax(0, 1fr); gap: 2px 12px; align-items: baseline; }
.hx-cfgerr-where { font-size: 12px; color: var(--muted); font-family: var(--mono); grid-row: span 2; }
.hx-cfgerr-code { font-size: 13px; color: var(--critical); overflow-wrap: anywhere; }
.hx-cfgerr-msg { font-size: 13px; }
.hx-line.file .ye-t { font-weight: 650; color: var(--muted); }
</style>
