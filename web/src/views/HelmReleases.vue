<script setup lang="ts">
// Helm releases, read from Helm's own release records: chart and version, status, history; values and the
// rendered manifest (secret-looking values masked, needs secrets.view); compare two revisions; rollback command.
import { computed, onMounted, reactive, ref, watch } from "vue";
import { agent, agentMessage } from "@/api/client";
import Badge from "@/components/Badge.vue";
import DataTable from "@/components/DataTable.vue";
import EmptyState from "@/components/EmptyState.vue";
import Icon from "@/components/Icon.vue";
import Modal from "@/components/Modal.vue";
import Skeleton from "@/components/Skeleton.vue";
import Toolbar from "@/components/Toolbar.vue";
import { diffLines, type DiffLine } from "@/lib/diff";
import { fmt } from "@/lib/format";
import { useFiltered } from "@/lib/list";
import { page } from "@/stores/prefs";
import { useCluster } from "@/stores/cluster";
import { useSession } from "@/stores/session";
import { toast } from "@/stores/ui";

const s = useSession(), state = page("helm", { key: "name", dir: 1 });
const list = ref<any[] | null>(null), error = ref("");
const api = () => agent(s.current!.id);
async function load() { try { list.value = await api().get("helm/releases"); error.value = ""; } catch (e: any) { error.value = agentMessage(e); } }
onMounted(load);
watch(() => s.current?.id, load);
const LEVEL: Record<string, string> = { deployed: "good", superseded: "neutral", failed: "critical", "pending-install": "warning", "pending-upgrade": "warning", "pending-rollback": "warning", uninstalling: "warning" };
const { rows, all, count } = useFiltered(() => list.value || undefined, state, (x: any) => `${x.name} ${x.namespace} ${x.chart} ${x.app_version}`,
  (x, f) => f === "all" || (f === "problems" ? x.status !== "deployed" : true));
const columns = [{ key: "name", label: "Release", cls: "primary" }, { key: "namespace", label: "Namespace" }, { key: "chart", label: "Chart" },
  { key: "app_version", label: "App version" }, { key: "status", label: "Status" }, { key: "revision", label: "Revision", num: true },
  { key: "updated", label: "Updated", sort: (x: any) => x.updated || "" }];

// --- one release -------------------------------------------------------------------------
const rel = reactive({ open: false, item: null as any, history: [] as any[], busy: false, err: "", shown: 0, view: "values" as "values" | "manifest" | "compare",
  data: {} as Record<number, any>, a: 0, b: 0 });
const canValues = computed(() => s.can("secrets.view"));
async function open(x: any) {
  Object.assign(rel, { open: true, item: x, history: [], err: "", shown: x.revision, view: "values", data: {}, a: 0, b: 0 });
  try {
    rel.history = await api().get(`helm/releases/${x.namespace}/${x.name}`);
    rel.b = rel.history[0]?.revision || 0; rel.a = rel.history[1]?.revision || rel.b;
    if (canValues.value) await revision(x.revision);
  } catch (e: any) { rel.err = e.message; }
}
async function revision(n: number) {
  rel.shown = n;
  if (rel.data[n] || !canValues.value) return;
  rel.busy = true;
  try { rel.data[n] = await api().get(`helm/releases/${rel.item.namespace}/${rel.item.name}/revisions/${n}`); }
  catch (e: any) { rel.err = e.message; } finally { rel.busy = false; }
}
async function compare() {
  rel.view = "compare";
  await Promise.all([revision(rel.a), revision(rel.b)]);
  rel.shown = rel.b;
}
const what = ref<"values" | "manifest">("manifest");
const diff = computed<DiffLine[]>(() => {
  const A = rel.data[rel.a], B = rel.data[rel.b];
  return A && B ? diffLines(A[what.value] || "", B[what.value] || "").filter((l, i, arr) => l.op !== " " || arr.slice(Math.max(0, i - 3), i + 4).some((x) => x.op !== " ")) : [];
});
// Real rollback when the agent has Helm turned on (K10Y_HELM_ENABLED + rbac-helm.yaml): dry run first, then confirm.
const cl = useCluster();
const canRollback = computed(() => Boolean(cl.info?.helm_enabled) && s.can("workloads.rollback") && !cl.isProtected(rel.item?.namespace));
const rb = reactive({ rev: 0, busy: false, dry: "", err: "" });
async function rollback(n: number, dry: boolean) {
  Object.assign(rb, { rev: n, busy: true, err: "", ...(dry ? { dry: "" } : {}) });
  try {
    const r = await api().post<any>(`helm/releases/${rel.item.namespace}/${rel.item.name}/rollback`, { revision: n, dry_run: dry });
    if (dry) rb.dry = r.message || "Dry run passed";
    else { toast(true, r.message || `Rolled back to #${n}`); rb.rev = 0; rb.dry = ""; await load(); await open(list.value?.find((x) => x.name === rel.item.name && x.namespace === rel.item.namespace) || rel.item); }
  } catch (e: any) { rb.err = e.message; } finally { rb.busy = false; }
}
// Upgrade with edited values (same chart): dry run shows the manifest diff, then upgrade.
const canUpgrade = computed(() => canRollback.value && s.can("apply.yaml") && canValues.value);
const up = reactive({ open: false, text: "", busy: false, diff: "", msg: "", err: "", checked: "" });
function editValues() {
  const cur = rel.data[rel.item.revision];
  Object.assign(up, { open: true, text: cur?.values?.trim() === "{}" ? "" : cur?.values || "", busy: false, diff: "", msg: "", err: "", checked: "" });
}
async function upgrade(dry: boolean) {
  up.busy = true; up.err = "";
  try {
    const r = await api().post<any>(`helm/releases/${rel.item.namespace}/${rel.item.name}/upgrade`, { values: up.text, dry_run: dry });
    if (dry) { up.diff = r.diff || ""; up.msg = r.message; up.checked = up.text; }
    else { toast(true, r.message || "Upgraded"); up.open = false; await load(); await open(list.value?.find((x) => x.name === rel.item.name && x.namespace === rel.item.namespace) || rel.item); }
  } catch (e: any) { up.err = e.message; } finally { up.busy = false; }
}
const upLines = computed(() => up.diff.split("\n").filter((l) => !l.startsWith("---") && !l.startsWith("+++")));
const rollbackCmd = (n: number) => `helm rollback ${rel.item?.name} ${n} -n ${rel.item?.namespace}`;
async function copy(text: string) { try { await navigator.clipboard.writeText(text); toast(true, "Copied"); } catch { toast(false, "Copy failed"); } }
</script>

<template>
  <Toolbar :state="state" placeholder="Search releases, charts or versions" :namespaces="all.map((x: any) => x.namespace)" :filters="[['all', 'All'], ['problems', 'Not deployed']]" :count="count" />
  <div v-if="error" class="banner show"><Icon name="crit" /><span class="msg">{{ error }}</span></div>
  <section class="card flush"><div class="card-body table-wrap">
    <Skeleton v-if="!list && !error" :rows="5" />
    <DataTable v-else-if="list" v-model:sort="state.sort" :rows="rows" :columns="columns" clickable
      :empty-title="all.length ? 'No matching releases' : 'No Helm releases in this cluster'" empty-icon="tag" @row="open">
      <template #namespace="{ row }"><span class="ns">{{ row.namespace }}</span></template>
      <template #chart="{ row }"><span class="mono">{{ row.chart }}</span> <span class="dim">{{ row.chart_version }}</span></template>
      <template #status="{ row }"><Badge :level="LEVEL[row.status] || 'neutral'" :label="row.status || 'unknown'" /></template>
      <template #revision="{ row }">{{ row.revision }} <span class="dim">/ {{ row.revisions }}</span></template>
      <template #updated="{ row }"><span class="dim">{{ row.updated ? fmt.ago(row.updated) : "–" }}</span></template>
    </DataTable>
  </div></section>
  <p class="hint" style="margin-top:12px">Read from Helm's own release records (Secrets labelled <code>owner=helm</code>). Values and manifests need <b>secrets.view</b>, and anything that looks like a password, token or key is masked.</p>

  <Modal :open="rel.open" :title="rel.item ? `${rel.item.name} · ${rel.item.namespace}` : ''" wide @close="rel.open = false">
    <template v-if="rel.item">
      <div class="hr-head"><span class="mono">{{ rel.item.chart }} {{ rel.item.chart_version }}</span><span class="dim">app {{ rel.item.app_version || "–" }}</span>
        <Badge :level="LEVEL[rel.item.status] || 'neutral'" :label="rel.item.status" /><span class="grow" />
        <button v-if="canUpgrade" class="btn sm" type="button" :disabled="!rel.data[rel.item.revision]" @click="editValues">Edit values…</button></div>
      <div v-if="rel.err" class="test-result show err"><Icon name="crit" /><span>{{ rel.err }}</span></div>
      <div class="table-wrap card" style="box-shadow:none"><table class="data"><thead><tr><th>Revision</th><th>Status</th><th>Chart</th><th>App</th><th>Updated</th><th>Description</th><th /></tr></thead>
        <tbody><tr v-for="h in rel.history" :key="h.revision" :class="{ 'hr-sel': rel.view !== 'compare' && rel.shown === h.revision }">
          <td class="num">{{ h.revision }}</td><td><Badge :level="LEVEL[h.status] || 'neutral'" :label="h.status" /></td>
          <td class="mono">{{ h.chart_version }}</td><td>{{ h.app_version || "–" }}</td><td class="dim">{{ h.updated ? fmt.ago(h.updated) : "–" }}</td>
          <td class="hr-desc">{{ h.description }}</td>
          <td class="actions"><button v-if="canValues" class="btn ghost sm" type="button" @click="rel.view = rel.view === 'compare' ? 'values' : rel.view; revision(h.revision)">View</button>
            <button v-if="h.revision !== rel.item.revision && canRollback" class="btn ghost sm" type="button" :disabled="rb.busy" @click="rollback(h.revision, true)">Roll back…</button>
            <button v-else-if="h.revision !== rel.item.revision" class="btn ghost sm" type="button" :title="rollbackCmd(h.revision)" @click="copy(rollbackCmd(h.revision))">Copy rollback command</button></td></tr></tbody></table></div>
      <div v-if="rb.rev && (rb.dry || rb.err || rb.busy)" class="hr-rb">
        <div v-if="rb.err" class="test-result show err"><Icon name="crit" /><span>{{ rb.err }}</span></div>
        <div v-else-if="rb.dry" class="test-result show ok"><Icon name="ok" /><span>{{ rb.dry }}.</span></div>
        <p v-else class="hint">Checking with a dry run…</p>
        <div class="hr-row"><button class="btn ghost" type="button" @click="rb.rev = 0">Cancel</button><span class="grow" />
          <button class="btn primary" type="button" :disabled="rb.busy || !rb.dry" @click="rollback(rb.rev, false)">Roll back {{ rel.item.name }} to #{{ rb.rev }}</button></div>
      </div>

      <template v-if="canValues">
        <div class="hr-tabs">
          <div class="segmented sm" role="group">
            <button type="button" :aria-pressed="rel.view === 'values'" @click="rel.view = 'values'">Values</button>
            <button type="button" :aria-pressed="rel.view === 'manifest'" @click="rel.view = 'manifest'">Manifest</button>
            <button type="button" :aria-pressed="rel.view === 'compare'" :disabled="rel.history.length < 2" @click="compare">Compare revisions</button></div>
          <template v-if="rel.view === 'compare'">
            <select v-model.number="rel.a" class="select sm" aria-label="From revision" @change="compare"><option v-for="h in rel.history" :key="h.revision" :value="h.revision">#{{ h.revision }}</option></select>
            <span class="hint">→</span>
            <select v-model.number="rel.b" class="select sm" aria-label="To revision" @change="compare"><option v-for="h in rel.history" :key="h.revision" :value="h.revision">#{{ h.revision }}</option></select>
            <div class="segmented sm" role="group"><button type="button" :aria-pressed="what === 'manifest'" @click="what = 'manifest'">Manifest</button><button type="button" :aria-pressed="what === 'values'" @click="what = 'values'">Values</button></div></template>
          <span v-else class="hint">Revision #{{ rel.shown }}</span>
        </div>
        <p v-if="rel.busy" class="hint">Loading…</p>
        <template v-else-if="rel.view === 'compare'">
          <div class="ye-diff hx-diff" role="region" aria-label="Difference between revisions">
            <div v-if="!diff.length" class="ye-gap" style="padding-left:12px">No differences</div>
            <div v-for="(l, i) in diff" :key="i" class="ye-line hx-line" :class="{ add: l.op === '+', del: l.op === '-' }"><span class="ye-op">{{ l.op === "-" ? "−" : l.op === "+" ? "+" : "" }}</span><span class="ye-t">{{ l.text }}</span></div>
          </div></template>
        <pre v-else-if="rel.data[rel.shown]" class="logs hr-pre">{{ rel.data[rel.shown][rel.view] || (rel.view === "values" ? "# no values set: the chart's defaults are used" : "") }}</pre>
      </template>
      <p v-else class="hint" style="margin:0">Values and manifests need the <b>secrets.view</b> permission.</p>
      <p v-if="canRollback" class="hint" style="margin:0"><b>Roll back…</b> runs <code>helm rollback</code> on the agent, after a dry run, so Helm's hooks run and its history stays right. It's in the activity log.</p>
      <p v-else class="hint" style="margin:0"><b>Copy rollback command</b> gives you the <code>helm rollback</code> command. To roll back from here, turn on Helm on the agent (<code>K10Y_HELM_ENABLED=true</code> and <code>rbac-helm.yaml</code>).</p>
    </template>
    <template #actions><span style="flex:1" /><button class="btn" type="button" @click="rel.open = false">Close</button></template>
  </Modal>

  <Modal :open="up.open" :title="rel.item ? `Upgrade ${rel.item.name} with new values` : ''" wide @close="up.open = false">
    <p class="hint" style="margin:0">The values you set for this release (the chart's defaults still apply for anything not set). Values shown as <code>••••••</code> keep their real value; type a new one to change it. Same chart and version: {{ rel.item?.chart }} {{ rel.item?.chart_version }}.</p>
    <textarea v-model="up.text" class="ye-text hr-values" spellcheck="false" autocapitalize="off" aria-label="Release values (YAML)" />
    <div v-if="up.err" class="test-result show err"><Icon name="crit" /><span>{{ up.err }}</span></div>
    <template v-if="up.checked && up.checked === up.text && !up.err">
      <div class="test-result show ok"><Icon name="ok" /><span>{{ up.msg }}.</span></div>
      <div v-if="up.diff" class="ye-diff hx-diff" role="region" aria-label="Rendered objects: before and after">
        <div v-for="(l, i) in upLines" :key="i" class="ye-line hx-line" :class="{ add: l[0] === '+', del: l[0] === '-', hunk: l.startsWith('@@') }">
          <span class="ye-op">{{ l.startsWith("@@") ? "" : l[0] === "-" ? "−" : l[0] === "+" ? "+" : "" }}</span><span class="ye-t">{{ l.startsWith("@@") ? "⋯" : l.slice(1) }}</span></div></div>
    </template>
    <template #actions><button class="btn ghost" type="button" @click="up.open = false">Cancel</button><span style="flex:1" />
      <button class="btn" type="button" :disabled="up.busy" @click="upgrade(true)">{{ up.busy && up.checked !== up.text ? "Checking…" : "Dry run" }}</button>
      <button class="btn primary" type="button" :disabled="up.busy || up.checked !== up.text || !!up.err" @click="upgrade(false)">Upgrade</button></template>
  </Modal>
</template>

<style scoped>
.hr-head { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.hr-tabs { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.hr-pre { max-height: 420px; white-space: pre; }
.hr-values { min-height: 260px; }
.hr-rb { display: flex; flex-direction: column; gap: 10px; padding: 12px; border: 1px solid var(--border); border-radius: 10px; }
.hr-row { display: flex; gap: 8px; align-items: center; }
.hr-desc { white-space: normal; min-width: 160px; }
tr.hr-sel td { background: var(--surface-active); }
.select.sm { height: 30px; width: auto; padding: 0 8px; font-size: 13px; }
</style>
