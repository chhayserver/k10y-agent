<script setup lang="ts">
// Every HAProxy config change goes through here: HAProxy validates it, you see the exact diff, then apply.
// With other instances in the same group, the change can be previewed and applied on all of them.
import { computed, ref, watch } from "vue";
import Icon from "@/components/Icon.vue";
import Modal from "@/components/Modal.vue";
import { hx, type HxOp, type HxPlan } from "@/lib/haproxy";
import { toast } from "@/stores/ui";

const props = defineProps<{ open: boolean; instanceId: number; instanceName?: string; group?: any[]; title: string; ops: HxOp[]; danger?: boolean; note?: string }>();
const emit = defineEmits<{ close: []; done: [] }>();
type Result = HxPlan & { instance_id: number; name: string };
const results = ref<Result[] | null>(null), busy = ref(false), error = ref(""), also = ref<number[]>([]), shown = ref(0);

const ids = computed(() => [props.instanceId, ...also.value]);
async function preview() {
  results.value = null; error.value = ""; busy.value = true;
  try {
    results.value = ids.value.length > 1 ? (await hx.groupChanges(ids.value, props.ops, false, props.note)).results
      : [{ ...(await hx.changes(props.instanceId, props.ops, false, props.note)), instance_id: props.instanceId, name: props.instanceName || "" }];
  } catch (e: any) { error.value = e.message; } finally { busy.value = false; }
}
async function apply() {
  busy.value = true; error.value = "";
  try {
    if (ids.value.length > 1) {
      const r = (await hx.groupChanges(ids.value, props.ops, true, props.note)).results;
      const failed = r.find((x) => !x.applied);
      if (failed) { error.value = `${failed.name}: ${failed.error}. ${r.filter((x) => x.applied).map((x) => x.name).join(", ") || "No instance"} changed; the rest were not touched.`; results.value = r; emit("done"); return; }
      toast(true, `Applied on ${r.map((x) => x.name).join(", ")}. HAProxy reloaded.`);
    } else {
      const r = await hx.changes(props.instanceId, props.ops, true, props.note);
      toast(true, `Applied: ${r.summary.join(", ")}. HAProxy reloaded.`);
    }
    emit("done");
  } catch (e: any) { error.value = e.message; } finally { busy.value = false; }
}
watch(() => props.open, (o) => { if (o) { also.value = []; shown.value = 0; preview(); } });
watch(also, () => { if (props.open) { shown.value = 0; preview(); } }, { deep: true });
const plan = computed(() => results.value?.[shown.value]);
const allValid = computed(() => !!results.value?.length && results.value.every((r) => r.valid));
const lines = computed(() => (plan.value?.diff || "").split("\n").filter((l) => l && !l.startsWith("+++") && !l.startsWith("---")));
</script>
<template>
  <Modal :open="open" :title="title" wide @close="emit('close')">
    <div v-if="group?.length" class="form-row"><span class="lbl">Also apply to the same group</span>
      <div style="display:flex;gap:6px;flex-wrap:wrap"><label v-for="g in group" :key="g.id" class="pill"><input v-model="also" type="checkbox" :value="g.id"> {{ g.name }}</label></div></div>
    <div v-if="busy && !results" class="hint">Asking HAProxy to validate the change…</div>
    <template v-if="plan">
      <div v-if="results!.length > 1" class="segmented" role="tablist"><button v-for="(r, k) in results" :key="r.instance_id" :aria-pressed="shown === k" @click="shown = k">
        <Icon :name="r.valid ? 'ok' : 'crit'" :style="{ color: r.valid ? 'var(--good)' : 'var(--critical)', width: '14px', height: '14px' }" /> {{ r.name }}</button></div>
      <ul class="hx-summary"><li v-for="s in plan.summary || []" :key="s">{{ s }}</li></ul>
      <div class="test-result show" :class="plan.valid ? 'ok' : 'err'"><Icon :name="plan.valid ? 'ok' : 'crit'" />
        <span>{{ plan.valid ? (plan.applied ? "Applied." : "HAProxy validated the new configuration. Nothing has changed yet.") : `HAProxy rejects this configuration: ${plan.error}` }}</span></div>
      <div class="lbl" style="margin-top:4px">Configuration changes</div>
      <div class="ye-diff hx-diff" role="region" aria-label="Configuration diff">
        <div v-if="!lines.length" class="ye-gap" style="padding-left:12px">No changes</div>
        <div v-for="(l, i) in lines" :key="i" class="ye-line hx-line" :class="{ add: l[0] === '+', del: l[0] === '-', hunk: l.startsWith('@@') }">
          <span class="ye-op">{{ l.startsWith("@@") ? "" : l[0] === "-" ? "−" : l[0] === "+" ? "+" : "" }}</span><span class="ye-t">{{ l.startsWith("@@") ? "⋯" : l.slice(1) }}</span></div>
      </div>
      <p class="hint" style="margin:0">Applying commits the change and reloads HAProxy gracefully: open connections finish, new ones use the new config. If the reload fails, the previous configuration stays active.
        <template v-if="results!.length > 1"> Instances are changed one after another; if one fails, the rest are left untouched.</template></p>
    </template>
    <div v-if="error" class="test-result show err"><Icon name="crit" /><span>{{ error }}</span></div>
    <template #actions><button class="btn ghost" type="button" @click="emit('close')">Cancel</button><span style="flex:1" />
      <button class="btn" type="button" :disabled="busy" @click="preview">Validate again</button>
      <button class="btn" :class="danger ? 'danger' : 'primary'" type="button" :disabled="busy || !allValid" @click="apply">
        {{ busy && results ? "Applying…" : results && results.length > 1 ? `Apply on ${results.length} instances` : "Apply" }}</button></template>
  </Modal>
</template>
