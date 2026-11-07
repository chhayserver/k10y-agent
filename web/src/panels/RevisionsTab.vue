<script setup lang="ts">
// A Deployment's rollout history: every revision with its images, change cause and who changed it (from the
// activity log), a diff of the pod template between any two revisions, and roll back to a chosen revision.
import { computed, onMounted, ref, watch } from "vue";
import { agent, hub } from "@/api/client";
import Badge from "@/components/Badge.vue";
import Icon from "@/components/Icon.vue";
import { actRollback } from "@/lib/actions";
import { diffLines } from "@/lib/diff";
import { fmt } from "@/lib/format";
import { useSession } from "@/stores/session";

const props = defineProps<{ namespace: string; name: string }>();
const s = useSession();
const revs = ref<any[] | null>(null), audit = ref<any[]>([]), error = ref(""), a = ref(0), b = ref(0);
async function load() {
  try {
    revs.value = await agent(s.current!.id).get(`deployments/${props.namespace}/${props.name}/revisions`);
    b.value = revs.value![0]?.revision || 0; a.value = revs.value![1]?.revision || b.value;
  } catch (e: any) { error.value = e.message; }
  try { audit.value = await hub.get("/audit", { cluster_id: s.current!.id, target: `${props.namespace}/${props.name}`, limit: 200 }); } catch { audit.value = []; }
}
onMounted(load);
// Who made a revision: the k10y action on this Deployment closest before the revision appeared (within 10 minutes).
function who(r: any): string {
  if (!r.created) return "";
  const t = Date.parse(r.created);
  const hit = audit.value.filter((x) => x.ok && !x.dry_run && Date.parse(x.time) <= t + 60e3 && Date.parse(x.time) >= t - 600e3)
    .sort((x, y) => Date.parse(y.time) - Date.parse(x.time))[0];
  return hit ? `${hit.username} · ${hit.action}` : "";
}
const byRev = computed(() => new Map((revs.value || []).map((r) => [r.revision, r])));
const diff = computed(() => {
  const A = byRev.value.get(a.value), B = byRev.value.get(b.value);
  if (!A || !B) return [];
  const all = diffLines(A.template, B.template);
  return all.filter((l, i) => l.op !== " " || all.slice(Math.max(0, i - 2), i + 3).some((x) => x.op !== " "));
});
watch([a, b], () => { if (a.value === b.value && revs.value && revs.value.length > 1) a.value = revs.value.find((r) => r.revision !== b.value)!.revision; });
const canRollback = computed(() => s.can("workloads.rollback"));
</script>

<template>
  <div v-if="error" class="test-result show err"><Icon name="crit" /><span>{{ error }}</span></div>
  <p v-else-if="!revs" class="hint">Loading revisions…</p>
  <template v-else>
    <div class="table-wrap card" style="box-shadow:none"><table class="data"><thead><tr><th>Revision</th><th>Images</th><th>Created</th><th>Change</th><th /></tr></thead>
      <tbody><tr v-for="r in revs" :key="r.revision">
        <td class="num"><b>#{{ r.revision }}</b> <Badge v-if="r.current" level="good" label="current" /></td>
        <td class="mono rv-img">{{ r.images.join(", ") }}</td>
        <td class="dim">{{ r.created ? fmt.ago(r.created) : "–" }}</td>
        <td class="rv-why">{{ r.change_cause || who(r) || "–" }}</td>
        <td class="actions"><button v-if="canRollback && !r.current" class="btn ghost sm" type="button" @click="actRollback({ namespace, name }, r.revision)">Roll back to #{{ r.revision }}</button></td></tr></tbody></table></div>
    <div v-if="revs.length > 1" class="rv-cmp">
      <span class="lbl">Compare</span>
      <select v-model.number="a" class="select sm" aria-label="From revision"><option v-for="r in revs" :key="r.revision" :value="r.revision">#{{ r.revision }}</option></select>
      <span class="hint">→</span>
      <select v-model.number="b" class="select sm" aria-label="To revision"><option v-for="r in revs" :key="r.revision" :value="r.revision">#{{ r.revision }}</option></select>
      <span class="hint">pod template</span>
    </div>
    <div v-if="revs.length > 1" class="ye-diff hx-diff" role="region" aria-label="Difference between revisions">
      <div v-if="!diff.length" class="ye-gap" style="padding-left:12px">The pod templates are the same.</div>
      <div v-for="(l, i) in diff" :key="i" class="ye-line hx-line" :class="{ add: l.op === '+', del: l.op === '-' }"><span class="ye-op">{{ l.op === "-" ? "−" : l.op === "+" ? "+" : "" }}</span><span class="ye-t">{{ l.text }}</span></div>
    </div>
    <p class="hint" style="margin:8px 0 0">Kubernetes keeps the last {{ revs.length }} revisions (<code>revisionHistoryLimit</code>). "Change" is the <code>kubernetes.io/change-cause</code> annotation, or the k10y action that made it.</p>
  </template>
</template>

<style scoped>
.rv-img { white-space: normal; overflow-wrap: anywhere; min-width: 180px; }
.rv-why { white-space: normal; min-width: 140px; }
.rv-cmp { display: flex; align-items: center; gap: 8px; margin: 14px 0 8px; flex-wrap: wrap; }
.rv-cmp .lbl { font-weight: 600; font-size: 13px; }
.select.sm { height: 30px; width: auto; padding: 0 8px; font-size: 13px; }
</style>
