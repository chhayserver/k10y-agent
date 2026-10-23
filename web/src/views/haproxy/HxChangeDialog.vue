<script setup lang="ts">
// Every HAProxy config change goes through here: HAProxy validates it, you see the exact diff, then apply.
import { computed, ref, watch } from "vue";
import Icon from "@/components/Icon.vue";
import Modal from "@/components/Modal.vue";
import { hx, type HxOp } from "@/lib/haproxy";
import { toast } from "@/stores/ui";

const props = defineProps<{ open: boolean; instanceId: number; title: string; ops: HxOp[]; danger?: boolean }>();
const emit = defineEmits<{ close: []; done: [] }>();
const plan = ref<Awaited<ReturnType<typeof hx.changes>> | null>(null), busy = ref(false), error = ref("");

async function preview() {
  plan.value = null; error.value = ""; busy.value = true;
  try { plan.value = await hx.changes(props.instanceId, props.ops, false); }
  catch (e: any) { error.value = e.message; } finally { busy.value = false; }
}
async function apply() {
  busy.value = true; error.value = "";
  try {
    const r = await hx.changes(props.instanceId, props.ops, true);
    toast(true, `Applied: ${r.summary.join(", ")}. HAProxy reloaded.`); emit("done");
  } catch (e: any) { error.value = e.message; } finally { busy.value = false; }
}
watch(() => props.open, (o) => { if (o) preview(); });
const lines = computed(() => (plan.value?.diff || "").split("\n").filter((l) => l && !l.startsWith("+++") && !l.startsWith("---")));
</script>
<template>
  <Modal :open="open" :title="title" wide @close="emit('close')">
    <div v-if="busy && !plan" class="hint">Asking HAProxy to validate the change…</div>
    <template v-if="plan">
      <ul class="hx-summary"><li v-for="s in plan.summary" :key="s">{{ s }}</li></ul>
      <div class="test-result show" :class="plan.valid ? 'ok' : 'err'"><Icon :name="plan.valid ? 'ok' : 'crit'" />
        <span>{{ plan.valid ? "HAProxy validated the new configuration. Nothing has changed yet." : `HAProxy rejects this configuration: ${plan.error}` }}</span></div>
      <div class="lbl" style="margin-top:4px">Configuration changes</div>
      <div class="ye-diff hx-diff" role="region" aria-label="Configuration diff">
        <div v-if="!lines.length" class="ye-gap" style="padding-left:12px">No changes</div>
        <div v-for="(l, i) in lines" :key="i" class="ye-line hx-line" :class="{ add: l[0] === '+', del: l[0] === '-', hunk: l.startsWith('@@') }">
          <span class="ye-op">{{ l.startsWith("@@") ? "" : l[0] === "-" ? "−" : l[0] === "+" ? "+" : "" }}</span><span class="ye-t">{{ l.startsWith("@@") ? "⋯" : l.slice(1) }}</span></div>
      </div>
      <p class="hint" style="margin:0">Applying commits the change and reloads HAProxy gracefully: open connections finish, new ones use the new config. If the reload fails, the previous configuration stays active.</p>
    </template>
    <div v-if="error" class="test-result show err"><Icon name="crit" /><span>{{ error }}</span></div>
    <template #actions><button class="btn ghost" type="button" @click="emit('close')">Cancel</button><span style="flex:1" />
      <button class="btn" type="button" :disabled="busy" @click="preview">Validate again</button>
      <button class="btn" :class="danger ? 'danger' : 'primary'" type="button" :disabled="busy || !plan?.valid" @click="apply">{{ busy && plan ? "Applying…" : "Apply" }}</button></template>
  </Modal>
</template>
