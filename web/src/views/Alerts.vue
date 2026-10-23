<script setup lang="ts">
import { to } from "@/lib/nav";
import { computed, onMounted, onUnmounted, reactive, ref } from "vue";
import { hub } from "@/api/client";
import Badge from "@/components/Badge.vue";
import DataTable from "@/components/DataTable.vue";
import Icon from "@/components/Icon.vue";
import Modal from "@/components/Modal.vue";
import Skeleton from "@/components/Skeleton.vue";
import Toolbar from "@/components/Toolbar.vue";
import { fmt, type Level } from "@/lib/format";
import { page } from "@/stores/prefs";
import { useSession } from "@/stores/session";
import { toast } from "@/stores/ui";

const s = useSession(), state = page("alerts", { key: "id", dir: -1 }, "open"), rstate = page("alert-rules", { key: "name", dir: 1 });
const canManage = computed(() => s.canGlobal("alerts.manage"));
const events = ref<any[] | null>(null), engine = ref<any>(null), rules = ref<any[] | null>(null), channels = ref<any[]>([]), error = ref("");
const tab = ref<"events" | "rules">("events");

async function load() {
  try {
    const [ev, ru, ch] = await Promise.all([hub.get("/alerts/events", { limit: 500 }), hub.get("/alerts/rules"), hub.get("/alerts/channels/names")]);
    events.value = ev.events; engine.value = ev.engine; rules.value = ru; channels.value = ch; error.value = "";
  } catch (e: any) { error.value = e.message; }
}
let timer = 0;
onMounted(() => { load(); timer = window.setInterval(load, 30000); });
onUnmounted(() => clearInterval(timer));

const SEV: Record<string, Level> = { critical: "critical", warning: "warning", info: "neutral" };
const stateLevel = (e: any): [Level, string] => e.state === "firing" ? [SEV[e.severity] || "warning", "Firing"] : e.state === "pending" ? ["pending", "Pending"] : ["good", "Resolved"];
const rows = computed(() => {
  const q = state.q.trim().toLowerCase();
  return (events.value || []).filter((e) => (state.filter === "all" || (state.filter === "open" ? e.state !== "resolved" : e.state === state.filter))
    && (!q || `${e.title} ${e.rule} ${e.cluster} ${e.message}`.toLowerCase().includes(q)));
});
const firing = computed(() => (events.value || []).filter((e) => e.state === "firing").length);
const clusterName = (id: number | null) => (id == null ? "All clusters" : s.clusters.find((c: any) => c.id === id)?.name || `#${id}`);
const TYPE_LABEL: Record<string, string> = { problems: "Cluster problems", resource: "CPU / memory threshold", unreachable: "Cluster unreachable" };
function describe(r: any) {
  const p = r.params || {};
  if (r.type === "resource") return `${p.metric === "cpu" ? "CPU" : "Memory"} ≥ ${p.threshold}% on ${p.scope === "cluster" ? "the cluster" : "any node"}`;
  if (r.type === "problems") {
    const bits = [p.kinds?.length ? p.kinds.join(", ") : "Any kind"];
    if (p.namespaces?.length) bits.push(`in ${p.namespaces.join(", ")}`);
    if (p.exclude_namespaces?.length) bits.push(`except ${p.exclude_namespaces.join(", ")}`);
    return bits.join(" ");
  }
  return "Agent does not answer";
}

const evColumns = [{ key: "id", label: "Started" }, { key: "state", label: "State", sort: (e: any) => ({ firing: 0, pending: 1, resolved: 2 } as any)[e.state] },
  { key: "title", label: "Alert", cls: "primary wrap" }, { key: "cluster", label: "Cluster" }, { key: "rule", label: "Rule" },
  { key: "last_seen", label: "Duration", nosort: true }, { key: "notified_at", label: "Notified" }];
const ruleColumns = [{ key: "name", label: "Name", cls: "primary" }, { key: "enabled", label: "Status" }, { key: "type", label: "Condition", cls: "wrap" },
  { key: "cluster_id", label: "Clusters" }, { key: "severity", label: "Severity" }, { key: "channel_ids", label: "Notify", nosort: true },
  { key: "_a", label: "Actions", nosort: true, cls: "actions" }];
function duration(e: any) {
  const end = e.resolved_at ? Date.parse(e.resolved_at) : Date.now();
  return fmt.age(Math.max(0, Math.round((end - Date.parse(e.first_seen)) / 1000)));
}
const detail = ref<any>(null);

// rule editor
const KINDS = ["Pod", "Deployment", "StatefulSet", "DaemonSet", "Job", "Node"];
const blank = () => ({ open: false, id: 0, name: "", enabled: true, type: "problems", cluster_id: null as number | null, severity: "warning",
  for_minutes: 2, repeat_minutes: 0, notify_resolved: true, ai_summary: false, channel_ids: [] as number[],
  kinds: [] as string[], namespaces: "", exclude: "kube-system", metric: "memory", scope: "node", threshold: 90, busy: false, msg: "" });
const edit = reactive(blank());
const list = (v: string) => v.split(",").map((x) => x.trim()).filter(Boolean);
function openNew() { Object.assign(edit, blank(), { open: true, channel_ids: channels.value.map((c) => c.id) }); }
function openEdit(r: any) {
  if (!canManage.value) return;
  const p = r.params || {};
  Object.assign(edit, blank(), r, { open: true, kinds: p.kinds || [], namespaces: (p.namespaces || []).join(", "), exclude: (p.exclude_namespaces || []).join(", "),
    metric: p.metric || "memory", scope: p.scope || "node", threshold: p.threshold ?? 90, channel_ids: [...(r.channel_ids || [])] });
}
async function save() {
  edit.busy = true; edit.msg = "";
  const params = edit.type === "problems" ? { kinds: edit.kinds, namespaces: list(edit.namespaces), exclude_namespaces: list(edit.exclude) }
    : edit.type === "resource" ? { metric: edit.metric, scope: edit.scope, threshold: Number(edit.threshold) } : {};
  const body = { name: edit.name, enabled: edit.enabled, type: edit.type, params, cluster_id: edit.cluster_id, severity: edit.severity,
    for_minutes: Number(edit.for_minutes), repeat_minutes: Number(edit.repeat_minutes), notify_resolved: edit.notify_resolved, ai_summary: edit.ai_summary, channel_ids: edit.channel_ids };
  try {
    if (edit.id) await hub.patch(`/alerts/rules/${edit.id}`, body); else await hub.post("/alerts/rules", body);
    edit.open = false; toast(true, `Saved rule ${edit.name}`); await load();
  } catch (e: any) { edit.msg = e.message; } finally { edit.busy = false; }
}
async function toggle(r: any) {
  try { await hub.patch(`/alerts/rules/${r.id}`, { ...r, enabled: !r.enabled }); await load(); } catch (e: any) { toast(false, e.message); }
}
async function remove(r: any) {
  if (!confirm(`Delete alert rule ${r.name}? Its open alerts are closed.`)) return;
  try { await hub.del(`/alerts/rules/${r.id}`); toast(true, `Deleted ${r.name}`); await load(); } catch (e: any) { toast(false, e.message); }
}
function addPreset(kind: "problems" | "memory" | "unreachable") {
  openNew();
  if (kind === "problems") Object.assign(edit, { name: "Broken workloads", severity: "critical" });
  if (kind === "memory") Object.assign(edit, { name: "Node memory high", type: "resource", metric: "memory", scope: "node", threshold: 90, for_minutes: 5 });
  if (kind === "unreachable") Object.assign(edit, { name: "Cluster unreachable", type: "unreachable", severity: "critical", for_minutes: 2 });
}
</script>
<template>
  <div class="toolbar">
    <div class="segmented" role="group" aria-label="View">
      <button :aria-pressed="tab === 'events'" @click="tab = 'events'">Alerts<span v-if="firing" class="count-pill">{{ firing }}</span></button>
      <button :aria-pressed="tab === 'rules'" @click="tab = 'rules'">Rules<span v-if="rules" class="count-pill neutral">{{ rules.length }}</span></button></div>
    <span class="hint" v-if="engine">Checked {{ engine.last_run ? fmt.ago(engine.last_run) : "not yet" }} · every {{ engine.interval_s }}s<template v-if="engine.error"> · <span style="color:var(--critical)">{{ engine.error }}</span></template></span>
    <span class="grow" />
    <button class="btn" @click="load">Reload</button>
    <button v-if="canManage" class="btn primary" @click="openNew(); tab = 'rules'"><Icon name="bell" /><span>New rule</span></button>
  </div>
  <div v-if="error" class="banner show">{{ error }}</div>
  <div v-if="canManage && channels.length === 0 && rules" class="banner show info">No notification channel yet. Alerts are still recorded here; add a Telegram bot in <RouterLink :to="to('system')">System settings</RouterLink> to get messages.</div>

  <template v-if="tab === 'events'">
    <Toolbar :state="state" placeholder="Search alert, rule or cluster" :filters="[['open', 'Open'], ['firing', 'Firing'], ['resolved', 'Resolved'], ['all', 'All']]" :count="events ? `${rows.length} of ${events.length}` : ''" />
    <section class="card flush"><div class="card-body table-wrap">
      <Skeleton v-if="!events && !error" :rows="5" />
      <DataTable v-else-if="events" v-model:sort="state.sort" :rows="rows" :columns="evColumns" :row-key="(e) => String(e.id)" clickable
        :empty-title="state.filter === 'open' ? 'No open alerts' : 'No alerts'" :empty-sub="rules?.length ? 'Everything your rules watch looks fine.' : 'Create a rule to start watching your clusters.'" empty-icon="bell" :empty-good="state.filter === 'open'" @row="(e) => (detail = e)">
        <template #id="{ row }">{{ fmt.dateTime(row.first_seen) }}</template>
        <template #state="{ row }"><Badge :level="stateLevel(row)[0]" :label="stateLevel(row)[1]" /></template>
        <template #title="{ row }"><span>{{ row.title }}</span></template>
        <template #cluster="{ row }"><span class="ns">{{ row.cluster }}</span></template>
        <template #last_seen="{ row }">{{ duration(row) }}</template>
        <template #notified_at="{ row }"><span v-if="row.notify_error" class="chip" style="color:var(--critical)" :title="row.notify_error">Failed</span>
          <span v-else-if="row.notified_at" class="dim">{{ fmt.ago(row.notified_at) }}</span><span v-else class="dim">–</span></template>
      </DataTable>
    </div></section>
    <p class="hint" style="margin-top:12px">Pending alerts wait for the rule's hold time before a message is sent, so short blips stay quiet. History is kept in PostgreSQL.</p>
  </template>

  <template v-else>
    <section class="card flush"><div class="card-body table-wrap">
      <Skeleton v-if="!rules" :rows="3" />
      <template v-else-if="!rules.length">
        <div class="empty"><Icon name="bell" /><div class="title">No alert rules yet</div>
          <div v-if="canManage" class="s" style="display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin-top:10px">Start from a preset:
            <button class="btn sm" @click="addPreset('problems')">Broken workloads</button><button class="btn sm" @click="addPreset('memory')">Node memory ≥ 90%</button>
            <button class="btn sm" @click="addPreset('unreachable')">Cluster unreachable</button></div></div>
      </template>
      <DataTable v-else v-model:sort="rstate.sort" :rows="rules" :columns="ruleColumns" :row-key="(r) => String(r.id)" :clickable="canManage" @row="openEdit">
        <template #enabled="{ row }"><Badge :level="row.enabled ? 'good' : 'neutral'" :label="row.enabled ? 'Enabled' : 'Paused'" /></template>
        <template #type="{ row }"><div><div>{{ TYPE_LABEL[row.type] }}</div><div class="dim" style="font-size:12px">{{ describe(row) }} · for {{ row.for_minutes }} min</div></div></template>
        <template #cluster_id="{ row }">{{ clusterName(row.cluster_id) }}</template>
        <template #severity="{ row }"><Badge :level="SEV[row.severity]" :label="row.severity" /></template>
        <template #channel_ids="{ row }"><span v-if="!row.channel_ids.length" class="dim">History only</span>
          <span v-for="id in row.channel_ids" :key="id" class="chip">{{ channels.find((c) => c.id === id)?.name || "removed" }}</span>
          <span v-if="row.ai_summary" class="chip"><Icon name="ai" /> AI</span></template>
        <template #_a="{ row }"><template v-if="canManage"><button class="btn ghost sm" @click="toggle(row)">{{ row.enabled ? "Pause" : "Enable" }}</button>
          <button class="btn ghost sm" style="color:var(--critical)" @click="remove(row)">Delete</button></template></template>
      </DataTable>
    </div></section>
  </template>

  <Modal :open="!!detail" :title="detail?.title || ''" wide @close="detail = null">
    <template v-if="detail">
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center"><Badge :level="stateLevel(detail)[0]" :label="stateLevel(detail)[1]" /><Badge :level="SEV[detail.severity]" :label="detail.severity" />
        <span class="ns">{{ detail.cluster }}</span><span class="dim">Rule: {{ detail.rule }}</span></div>
      <div class="kv" style="margin:14px 0">
        <div><span>Started</span><b>{{ fmt.dateTime(detail.first_seen) }}</b></div>
        <div><span>Fired</span><b>{{ fmt.dateTime(detail.fired_at) }}</b></div>
        <div><span>Resolved</span><b>{{ fmt.dateTime(detail.resolved_at) }}</b></div>
        <div><span>Last seen</span><b>{{ fmt.dateTime(detail.last_seen) }}</b></div>
        <div><span>Notified</span><b>{{ fmt.dateTime(detail.notified_at) }}</b></div>
        <div v-if="detail.resource"><span>Object</span><b><code>{{ detail.resource.replace(/\/\//, "/") }}</code></b></div>
      </div>
      <div v-if="detail.notify_error" class="test-result show err"><Icon name="crit" /><span>{{ detail.notify_error }}</span></div>
      <p v-if="detail.message && detail.message !== detail.title" class="hint" style="white-space:pre-wrap">{{ detail.message }}</p>
      <div v-if="detail.ai_summary" class="card" style="padding:12px"><div class="lbl"><Icon name="ai" /> AI diagnosis</div><p style="white-space:pre-wrap;margin:6px 0 0">{{ detail.ai_summary }}</p></div>
    </template>
    <template #actions><span style="flex:1" /><button class="btn" type="button" @click="detail = null">Close</button></template>
  </Modal>

  <Modal :open="edit.open" :title="edit.id ? `Edit rule ${edit.name}` : 'New alert rule'" wide @close="edit.open = false">
    <div class="form-row"><label class="lbl">Name</label><input v-model="edit.name" class="input" placeholder="e.g. Broken pods in production"></div>
    <div class="form-row"><label class="lbl">Watch for</label>
      <div class="segmented" role="group"><button v-for="(l, t) in TYPE_LABEL" :key="t" type="button" :aria-pressed="edit.type === t" @click="edit.type = String(t)">{{ l }}</button></div></div>
    <template v-if="edit.type === 'problems'">
      <div class="form-row"><label class="lbl">Kinds (none selected = all)</label><div style="display:flex;gap:6px;flex-wrap:wrap">
        <label v-for="k in KINDS" :key="k" class="pill"><input v-model="edit.kinds" type="checkbox" :value="k"> {{ k }}</label></div>
        <span class="hint">Problems are what the Overview shows: crash loops, image pull errors, unavailable replicas, failed jobs, NotReady nodes…</span></div>
      <div class="two-col"><div class="form-row"><label class="lbl">Only namespaces</label><input v-model="edit.namespaces" class="input" placeholder="all, or e.g. prod, payments"></div>
        <div class="form-row"><label class="lbl">Skip namespaces</label><input v-model="edit.exclude" class="input" placeholder="e.g. kube-system"></div></div>
    </template>
    <div v-else-if="edit.type === 'resource'" class="two-col">
      <div class="form-row"><label class="lbl">Metric</label><select v-model="edit.metric" class="select"><option value="memory">Memory</option><option value="cpu">CPU</option></select></div>
      <div class="form-row"><label class="lbl">Where</label><select v-model="edit.scope" class="select"><option value="node">Any node</option><option value="cluster">Whole cluster</option></select></div>
      <div class="form-row"><label class="lbl">Threshold (%)</label><input v-model.number="edit.threshold" class="input" type="number" min="1" max="100"></div>
    </div>
    <p v-else class="hint">Fires when the hub cannot reach the cluster's agent.</p>
    <div class="two-col">
      <div class="form-row"><label class="lbl">Clusters</label><select v-model="edit.cluster_id" class="select"><option :value="null">All clusters</option>
        <option v-for="c in s.clusters" :key="c.id" :value="c.id">{{ c.name }}</option></select></div>
      <div class="form-row"><label class="lbl">Severity</label><select v-model="edit.severity" class="select"><option value="critical">Critical</option><option value="warning">Warning</option><option value="info">Info</option></select></div>
      <div class="form-row"><label class="lbl">Hold time (minutes)</label><input v-model.number="edit.for_minutes" class="input" type="number" min="0" max="1440"><span class="hint">Must stay true this long before it fires.</span></div>
      <div class="form-row"><label class="lbl">Remind every (minutes)</label><input v-model.number="edit.repeat_minutes" class="input" type="number" min="0"><span class="hint">0 = only once.</span></div>
    </div>
    <div class="form-row"><label class="lbl">Notify</label><div style="display:flex;gap:6px;flex-wrap:wrap">
      <label v-for="c in channels" :key="c.id" class="pill"><input v-model="edit.channel_ids" type="checkbox" :value="c.id"> {{ c.name }} <span class="dim">({{ c.type }})</span></label>
      <span v-if="!channels.length" class="hint">No channels yet. Add a Telegram bot in System settings.</span></div></div>
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <label class="pill"><input v-model="edit.notify_resolved" type="checkbox"> Send a message when it resolves</label>
      <label class="pill"><input v-model="edit.ai_summary" type="checkbox"> Add an AI diagnosis to the message</label>
      <label class="pill"><input v-model="edit.enabled" type="checkbox"> Enabled</label></div>
    <div v-if="edit.msg" class="test-result show err"><Icon name="crit" /><span>{{ edit.msg }}</span></div>
    <template #actions><button class="btn ghost" type="button" @click="edit.open = false">Cancel</button><span style="flex:1" />
      <button class="btn primary" :disabled="edit.busy || !edit.name" @click="save">{{ edit.id ? "Save" : "Create rule" }}</button></template>
  </Modal>
</template>
