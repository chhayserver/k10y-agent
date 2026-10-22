<script setup lang="ts">
// Edit an object's YAML (like kubectl edit): review a diff, dry run, then save. The agent replaces the object
// with a resourceVersion check, so a concurrent change is reported instead of overwritten.
import { computed, ref } from "vue";
import { agent } from "@/api/client";
import { diffLines, hunks } from "@/lib/diff";
import { useSession } from "@/stores/session";
import { toast } from "@/stores/ui";
import Icon from "./Icon.vue";

const props = defineProps<{ kind: string; namespace?: string | null; name: string; yaml: string }>();
const emit = defineEmits<{ done: [saved: boolean] }>();
const s = useSession();
const text = ref(props.yaml), step = ref<"edit" | "review">("edit"), busy = ref(false);
const result = ref<{ ok: boolean; text: string } | null>(null);
const changes = computed(() => diffLines(props.yaml, text.value));
const changed = computed(() => changes.value.filter((l) => l.op !== " ").length);
const shown = computed(() => hunks(changes.value));

function onKey(e: KeyboardEvent) {
  const el = e.target as HTMLTextAreaElement;
  if (e.key === "Tab" && !e.shiftKey) {          // YAML uses spaces: insert two instead of leaving the field
    e.preventDefault();
    const { selectionStart: a, selectionEnd: b } = el;
    text.value = text.value.slice(0, a) + "  " + text.value.slice(b);
    requestAnimationFrame(() => el.setSelectionRange(a + 2, a + 2));
  } else if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); review(); }
}
function review() { result.value = null; step.value = "review"; }
async function send(dry: boolean) {
  busy.value = true; result.value = null;
  try {
    const r = await agent(s.current!.id).put("resource", { kind: props.kind, namespace: props.namespace || null, name: props.name, yaml: text.value, dry_run: dry });
    result.value = { ok: true, text: dry ? `Dry run OK: ${r.message}` : r.message };
    if (!dry) { toast(true, r.message); emit("done", true); }
  } catch (e: any) { result.value = { ok: false, text: e.message }; } finally { busy.value = false; }
}
</script>
<template>
  <div class="yaml-editor">
    <div class="ye-bar">
      <div class="segmented" role="group"><button :aria-pressed="step === 'edit'" @click="step = 'edit'">Edit</button>
        <button :aria-pressed="step === 'review'" :disabled="!changed" @click="review">Review changes<span v-if="changed" class="count-pill neutral">{{ changed }}</span></button></div>
      <span class="hint" style="flex:1">{{ step === "edit" ? "Tab inserts spaces · Ctrl/⌘+Enter to review" : "Lines starting with + are added, − are removed." }}</span>
      <button class="btn ghost" @click="emit('done', false)">Cancel</button>
      <template v-if="step === 'review'">
        <button class="btn" :disabled="busy" @click="send(true)">Dry run</button>
        <button class="btn primary" :disabled="busy || !changed" @click="send(false)">Save</button></template>
      <button v-else class="btn primary" :disabled="!changed" @click="review">Review</button>
    </div>
    <div v-if="result" class="test-result show" :class="result.ok ? 'ok' : 'err'"><Icon :name="result.ok ? 'ok' : 'crit'" /><span>{{ result.text }}</span></div>
    <textarea v-if="step === 'edit'" v-model="text" class="ye-text" spellcheck="false" autocapitalize="off" autocomplete="off"
      :aria-label="`YAML of ${kind} ${name}`" @keydown="onKey" />
    <div v-else class="ye-diff" role="region" aria-label="Changes">
      <template v-for="(l, i) in shown" :key="i">
        <div v-if="l === null" class="ye-gap">⋯</div>
        <div v-else class="ye-line" :class="{ add: l.op === '+', del: l.op === '-' }"><span class="ye-no">{{ l.op === "+" ? "" : l.a }}</span><span class="ye-no">{{ l.op === "-" ? "" : l.b }}</span><span class="ye-op">{{ l.op === "-" ? "−" : l.op }}</span><span class="ye-t">{{ l.text }}</span></div>
      </template>
    </div>
  </div>
</template>
