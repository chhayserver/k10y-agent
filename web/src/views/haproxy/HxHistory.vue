<script setup lang="ts">
// Every configuration version k10y has seen: who changed what, the diff, and one-click rollback.
import { computed, onMounted, ref, watch } from "vue";
import Badge from "@/components/Badge.vue";
import EmptyState from "@/components/EmptyState.vue";
import Icon from "@/components/Icon.vue";
import { diffLines, hunks } from "@/lib/diff";
import { fmt, type Level } from "@/lib/format";
import { hx, type HxOp } from "@/lib/haproxy";

const props = defineProps<{ instanceId: number; canEdit: boolean }>();
const emit = defineEmits<{ review: [ops: HxOp[], title: string, note: string] }>();
const versions = ref<any[] | null>(null), selected = ref<number | null>(null), error = ref("");
const texts = ref<Record<number, string>>({});
async function load() {
  try { versions.value = await hx.versions(props.instanceId); error.value = ""; if (selected.value == null && versions.value.length) selected.value = versions.value[0].id; }
  catch (e: any) { error.value = e.message; }
}
async function text(id: number) {
  if (!(id in texts.value)) texts.value[id] = (await hx.version(props.instanceId, id)).text;
  return texts.value[id];
}
onMounted(load);
defineExpose({ load });
const idx = computed(() => (versions.value || []).findIndex((v) => v.id === selected.value));
const cur = computed(() => (versions.value || [])[idx.value]);
const prev = computed(() => (versions.value || [])[idx.value + 1]);
const diff = ref<ReturnType<typeof hunks> | null>(null), loadingDiff = ref(false);
watch(selected, async () => {
  diff.value = null;
  if (!cur.value || !props.canEdit) return;
  loadingDiff.value = true;
  try {
    const now = await text(cur.value.id), before = prev.value ? await text(prev.value.id) : "";
    diff.value = hunks(diffLines(before, now));
  } catch (e: any) { error.value = e.message; } finally { loadingDiff.value = false; }
});
const SOURCE: Record<string, [Level, string]> = { k10y: ["good", "k10y"], external: ["warning", "Outside k10y"], initial: ["neutral", "First seen"],
  rollback: ["serious", "Rollback"], sync: ["pending", "Kubernetes sync"] };
async function restore() {
  const v = cur.value; if (!v) return;
  emit("review", [{ op: "raw", kind: "config", data: { text: await text(v.id) } }], `Restore configuration version ${v.version}`, `roll back to version ${v.version}`);
}
</script>
<template>
  <div class="toolbar"><span class="hint">Every configuration applied from k10y, changed elsewhere, or synced from Kubernetes. Checked every 30 seconds.</span><span class="grow" />
    <button class="btn" @click="load"><Icon name="refresh" /><span>Reload</span></button></div>
  <div v-if="error" class="banner show"><Icon name="crit" /><span class="msg">{{ error }}</span></div>
  <EmptyState v-if="versions && !versions.length" title="No versions recorded yet" icon="rollback" sub="The first version is recorded within 30 seconds of connecting the HAProxy." />
  <div v-else-if="versions" class="hx-history">
    <section class="card flush"><ul class="list hx-versions">
      <li v-for="(v, i) in versions" :key="v.id" class="clickable" :class="{ on: v.id === selected }" @click="selected = v.id">
        <div style="min-width:0;flex:1"><div class="main-line">Version {{ v.version }} <Badge :level="SOURCE[v.source]?.[0] || 'neutral'" :label="SOURCE[v.source]?.[1] || v.source" />
          <Badge v-if="i === 0" level="good" label="Current" /></div>
          <div class="sub-line">{{ fmt.dateTime(v.created_at) }}<template v-if="v.username"> · {{ v.username }}</template></div>
          <div class="sub-line" style="white-space:normal">{{ v.summary }}</div></div></li></ul></section>
    <section class="card"><div class="card-body" style="display:flex;flex-direction:column;gap:10px">
      <template v-if="cur">
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><b>Version {{ cur.version }}</b><span class="hint">{{ prev ? `changes since version ${prev.version}` : "first recorded version" }}</span><span style="flex:1" />
          <button v-if="canEdit && idx > 0" class="btn primary" @click="restore"><Icon name="rollback" /><span>Restore this version…</span></button></div>
        <p v-if="!canEdit" class="hint">Seeing the configuration needs the <code>haproxy.configure</code> permission, because it can contain passwords.</p>
        <div v-else-if="loadingDiff" class="hint">Loading…</div>
        <div v-else-if="diff" class="ye-diff" style="max-height:600px">
          <div v-if="!diff.length" class="ye-gap" style="padding-left:12px">No differences</div>
          <template v-for="(l, i) in diff" :key="i"><div v-if="l === null" class="ye-gap">⋯</div>
            <div v-else class="ye-line" :class="{ add: l.op === '+', del: l.op === '-' }"><span class="ye-no">{{ l.op === "+" ? "" : l.a }}</span><span class="ye-no">{{ l.op === "-" ? "" : l.b }}</span>
              <span class="ye-op">{{ l.op === "-" ? "−" : l.op }}</span><span class="ye-t">{{ l.text }}</span></div></template>
        </div>
        <p v-if="canEdit && idx > 0" class="hint" style="margin:0">Restoring shows the full difference from the current configuration and is validated by HAProxy before it applies. It's recorded as a new version.</p>
      </template>
    </div></section>
  </div>
</template>
