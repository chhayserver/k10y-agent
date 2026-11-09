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
const silences = ref<any[]>([]);

async function loadHx() {
  if (canHx.value) try { hxInstances.value = await hub.get("/haproxy"); } catch { /* no access */ }
  if (canNg.value) try { ngInstances.value = await hub.get("/nginx"); } catch { /* no access */ }
}
onMounted(loadHx);
async function load() {
  try {
    const [ev, ru, ch, si] = await Promise.all([hub.get("/alerts/events", { limit: 500 }), hub.get("/alerts/rules"), hub.get("/alerts/channels/names"),
      hub.get<any[]>("/alerts/silences").catch(() => [])]);
    events.value = ev.events; engine.value = ev.engine; rules.value = ru; channels.value = ch; silences.value = si; error.value = "";
    if (detail.value) detail.value = ev.events.find((e: any) => e.id === detail.value.id) || detail.value;
  } catch (e: any) { error.value = e.message; }
}
let timer = 0;
onMounted(() => { load(); timer = window.setInterval(load, 30000); });
onUnmounted(() => clearInterval(timer));

// Acknowledge ("I'm on it": no more reminders) and silence (mute this alert, or its whole rule, for a while).
async function ack(e: any) {
  try { detail.value = { ...e, ...(await hub.post(`/alerts/events/${e.id}/ack`, {})) }; toast(true, "Acknowledged. Reminders stop and the channels are told."); await load(); }
  catch (err: any) { toast(false, err.message); }
}
const sil = reactive({ open: false, event: null as any, scope: "event", minutes: 60, reason: "", busy: false });
function openSilence(e: any) { Object.assign(sil, { open: true, event: e, scope: "event", minutes: 60, reason: "", busy: false }); }
async function silence() {
  sil.busy = true;
  try { await hub.post("/alerts/silences", { event_id: sil.event.id, scope: sil.scope, minutes: sil.minutes, reason: sil.reason }); sil.open = false; toast(true, "Silenced"); await load(); }
  catch (err: any) { toast(false, err.message); } finally { sil.busy = false; }
}
async function unsilence(x: any) { try { await hub.del(`/alerts/silences/${x.id}`); await load(); } catch (err: any) { toast(false, err.message); } }
const DURATIONS: [number, string][] = [[30, "30 min"], [60, "1 hour"], [240, "4 hours"], [1440, "1 day"], [10080, "1 week"]];

const SEV: Record<string, Level> = { critical: "critical", warning: "warning", info: "neutral" };
const stateLevel = (e: any): [Level, string] => e.state === "firing" ? [SEV[e.severity] || "warning", "Firing"] : e.state === "pending" ? ["pending", "Pending"] : ["good", "Resolved"];
const rows = computed(() => {
  const q = state.q.trim().toLowerCase();
  return (events.value || []).filter((e) => (state.filter === "all" || (state.filter === "open" ? e.state !== "resolved" : e.state === state.filter))
    && (!q || `${e.title} ${e.rule} ${e.cluster} ${e.message}`.toLowerCase().includes(q)));
});
const firing = computed(() => (events.value || []).filter((e) => e.state === "firing").length);
const clusterName = (id: number | null) => (id == null ? "All clusters" : s.clusters.find((c: any) => c.id === id)?.name || `#${id}`);
const TYPE_LABEL: Record<string, string> = { problems: "Cluster problems", resource: "CPU / memory threshold", unreachable: "Cluster unreachable", haproxy: "HAProxy", nginx: "Nginx" };
const NG_COND: Record<string, { label: string; unit?: string; def?: number; hint: string }> = {
  server_down: { label: "An upstream server doesn't answer", hint: "k10y's check (TCP connect every 10 s) fails for a server that isn't marked down." },
  upstream_down: { label: "An upstream has no server answering", hint: "None of an upstream's servers (backups aside) answers: its sites are failing." },
  error_rate: { label: "Server errors (5xx) above", unit: "% of responses", def: 5, hint: "Share of 5xx responses over the last 10 seconds, from the access log, at 1 request/s or more." },
  cert_expiry: { label: "Certificate expiring", unit: "days or less", def: 14, hint: "A certificate a site uses expires soon (or has expired)." },
  problems: { label: "Nginx stopped or config unreadable", hint: "Nginx isn't running, or its configuration can't be read." },
  unreachable: { label: "Agent unreachable", hint: "The hub can't reach the k10y agent next to this Nginx." },
};
const HX_COND: Record<string, { label: string; unit?: string; def?: number; hint: string }> = {
  server_down: { label: "A server is down", hint: "A server's health check fails." },
  backend_down: { label: "A backend has no server up", hint: "Every server of a backend is down or out of rotation: its frontend is failing." },
  error_rate: { label: "Server errors (5xx) above", unit: "% of responses", def: 5, hint: "Share of 5xx responses over the last 10 seconds, when the frontend gets at least 1 request/s." },
  sessions: { label: "Sessions near the limit", unit: "% of maxconn", def: 80, hint: "Current sessions as a share of the frontend's session limit." },
  cert_expiry: { label: "Certificate expiring", unit: "days or less", def: 14, hint: "A certificate stored on the HAProxy expires soon (or has expired)." },
  unreachable: { label: "HAProxy unreachable", hint: "The hub can't reach the HAProxy Data Plane API." },
};
const hxInstances = ref<any[]>([]);
const canHx = computed(() => s.canGlobal("haproxy.view"));
const ngInstances = ref<any[]>([]);
const canNg = computed(() => s.canGlobal("nginx.view"));
function describe(r: any) {
  const p = r.params || {};
  if (r.type === "resource") return `${p.metric === "cpu" ? "CPU" : "Memory"} ≥ ${p.threshold}% on ${p.scope === "cluster" ? "the cluster" : "any node"}`;
  if (r.type === "problems") {
    const bits = [p.kinds?.length ? p.kinds.join(", ") : "Any kind"];
    if (p.namespaces?.length) bits.push(`in ${p.namespaces.join(", ")}`);
    if (p.exclude_namespaces?.length) bits.push(`except ${p.exclude_namespaces.join(", ")}`);
    return bits.join(" ");
  }
  if (r.type === "nginx") {
    const c = NG_COND[p.condition] || { label: p.condition };
    const where = p.instance_id ? ngInstances.value.find((i) => i.id === p.instance_id)?.name || "one instance" : "any Nginx";
    return `${c.label}${c.unit ? ` ${p.threshold} ${c.unit}` : ""} · ${where}`;
  }
  if (r.type === "haproxy") {
    const c = HX_COND[p.condition] || { label: p.condition };
    const where = p.instance_id ? hxInstances.value.find((i) => i.id === p.instance_id)?.name || "one instance" : "any HAProxy";
    return `${c.label}${c.unit ? ` ${p.threshold} ${c.unit}` : ""} · ${where}`;
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
const KINDS = ["Pod", "Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "PersistentVolumeClaim", "Certificate", "Node"];
const blank = () => ({ open: false, id: 0, name: "", enabled: true, type: "problems", cluster_id: null as number | null, severity: "warning",
  for_minutes: 2, repeat_minutes: 0, notify_resolved: true, ai_summary: false, channel_ids: [] as number[],
  kinds: [] as string[], namespaces: "", exclude: "kube-system", metric: "memory", scope: "node", threshold: 90, busy: false, msg: "",
  hx_condition: "server_down", hx_instance: null as number | null, ng_condition: "server_down", ng_instance: null as number | null });
const edit = reactive(blank());
const list = (v: string) => v.split(",").map((x) => x.trim()).filter(Boolean);
function openNew() { Object.assign(edit, blank(), { open: true, channel_ids: channels.value.map((c) => c.id) }); }
function openEdit(r: any) {
  if (!canManage.value) return;
  const p = r.params || {};
  Object.assign(edit, blank(), r, { open: true, kinds: p.kinds || [], namespaces: (p.namespaces || []).join(", "), exclude: (p.exclude_namespaces || []).join(", "),
    metric: p.metric || "memory", scope: p.scope || "node", threshold: p.threshold ?? 90, channel_ids: [...(r.channel_ids || [])],
    hx_condition: p.condition || "server_down", hx_instance: p.instance_id ?? null, ng_condition: p.condition || "server_down", ng_instance: p.instance_id ?? null });
}
async function save() {
  edit.busy = true; edit.msg = "";
  const params = edit.type === "problems" ? { kinds: edit.kinds, namespaces: list(edit.namespaces), exclude_namespaces: list(edit.exclude) }
    : edit.type === "resource" ? { metric: edit.metric, scope: edit.scope, threshold: Number(edit.threshold) }
    : edit.type === "haproxy" ? { condition: edit.hx_condition, threshold: Number(edit.threshold), instance_id: edit.hx_instance }
    : edit.type === "nginx" ? { condition: edit.ng_condition, threshold: Number(edit.threshold), instance_id: edit.ng_instance } : {};
  if (edit.type === "haproxy" || edit.type === "nginx") edit.cluster_id = null;
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
  <div v-if="canManage && channels.length === 0 && rules" class="banner show info">No notification channel yet. Alerts are still recorded here; add Telegram, Slack, email or a webhook in <RouterLink :to="to('system')">System settings</RouterLink> to get messages.</div>

  <template v-if="tab === 'events'">
    <Toolbar :state="state" placeholder="Search alert, rule or cluster" :filters="[['open', 'Open'], ['firing', 'Firing'], ['resolved', 'Resolved'], ['all', 'All']]" :count="events ? `${rows.length} of ${events.length}` : ''" />
    <section class="card flush"><div class="card-body table-wrap">
      <Skeleton v-if="!events && !error" :rows="5" />
      <DataTable v-else-if="events" v-model:sort="state.sort" :rows="rows" :columns="evColumns" :row-key="(e) => String(e.id)" clickable
        :empty-title="state.filter === 'open' ? 'No open alerts' : 'No alerts'" :empty-sub="rules?.length ? 'Everything your rules watch looks fine.' : 'Create a rule to start watching your clusters.'" empty-icon="bell" :empty-good="state.filter === 'open'" @row="(e) => (detail = e)">
        <template #id="{ row }">{{ fmt.dateTime(row.first_seen) }}</template>
        <template #state="{ row }"><Badge :level="stateLevel(row)[0]" :label="stateLevel(row)[1]" />
          <span v-if="row.silenced_until" class="pill-tag" :title="`Silenced until ${fmt.dateTime(row.silenced_until)}`">silenced</span>
          <span v-else-if="row.acked_by && row.state !== 'resolved'" class="pill-tag" :title="`Acknowledged ${fmt.ago(row.acked_at)}`">{{ row.acked_by }} is on it</span></template>
        <template #title="{ row }"><span>{{ row.title }}</span></template>
        <template #cluster="{ row }"><span class="ns">{{ row.cluster }}</span></template>
        <template #last_seen="{ row }">{{ duration(row) }}</template>
        <template #notified_at="{ row }"><span v-if="row.notify_error" class="chip" style="color:var(--critical)" :title="row.notify_error">Failed</span>
          <span v-else-if="row.notified_at" class="dim">{{ fmt.ago(row.notified_at) }}</span><span v-else class="dim">–</span></template>
      </DataTable>
    </div></section>
    <section v-if="silences.length" class="card" style="margin-top:16px"><div class="card-head"><h2>Silences</h2><span class="hint">Matching alerts are still recorded, but nothing is sent.</span></div>
      <div class="card-body table-wrap"><table class="data"><thead><tr><th>Mutes</th><th>Until</th><th>Reason</th><th>By</th><th class="actions"><span class="sr-only">Actions</span></th></tr></thead>
        <tbody><tr v-for="x in silences" :key="x.id"><td>{{ x.title }}</td><td>{{ fmt.dateTime(x.until) }} <span class="dim">({{ fmt.ago(x.until) }})</span></td>
          <td>{{ x.reason || "–" }}</td><td class="dim">{{ x.created_by }}</td>
          <td class="actions"><button v-if="canManage" class="btn ghost sm" @click="unsilence(x)">End now</button></td></tr></tbody></table></div></section>
    <p class="hint" style="margin-top:12px">Pending alerts wait for the rule's hold time before a message is sent, so short blips stay quiet. <b>Acknowledge</b> stops reminders and tells the channels who's on it; <b>Silence</b> mutes an alert (also when it comes back) or its whole rule for a while. History is kept in PostgreSQL.</p>
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
        <template #cluster_id="{ row }">{{ row.type === 'haproxy' ? 'HAProxy' : row.type === 'nginx' ? 'Nginx' : clusterName(row.cluster_id) }}</template>
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
      <p v-if="detail.acked_by" class="hint"><Icon name="ok" /> Acknowledged by <b>{{ detail.acked_by }}</b> {{ fmt.ago(detail.acked_at) }}. No more reminders.</p>
      <p v-if="detail.silenced_until" class="hint"><Icon name="pause" /> Silenced until {{ fmt.dateTime(detail.silenced_until) }}.</p>
      <div v-if="detail.notify_error" class="test-result show err"><Icon name="crit" /><span>{{ detail.notify_error }}</span></div>
      <p v-if="detail.message && detail.message !== detail.title" class="hint" style="white-space:pre-wrap">{{ detail.message }}</p>
      <div v-if="detail.ai_summary" class="card" style="padding:12px"><div class="lbl"><Icon name="ai" /> AI diagnosis</div><p style="white-space:pre-wrap;margin:6px 0 0">{{ detail.ai_summary }}</p></div>
    </template>
    <template #actions>
      <template v-if="detail && detail.state !== 'resolved'">
        <button v-if="!detail.acked_by" class="btn" type="button" @click="ack(detail)"><Icon name="ok" /><span>Acknowledge</span></button>
        <button v-if="canManage" class="btn" type="button" @click="openSilence(detail)"><Icon name="pause" /><span>Silence…</span></button></template>
      <span style="flex:1" /><button class="btn" type="button" @click="detail = null">Close</button></template>
  </Modal>

  <Modal :open="sil.open" title="Silence alerts" @close="sil.open = false">
    <template v-if="sil.event">
      <div class="form-row"><span class="lbl">What</span>
        <label class="pill"><input v-model="sil.scope" type="radio" value="event"> This alert: {{ sil.event.title }} ({{ sil.event.cluster }}), also if it comes back</label>
        <label class="pill"><input v-model="sil.scope" type="radio" value="rule"> Every alert of rule {{ sil.event.rule }}</label></div>
      <div class="form-row"><span class="lbl">For</span><div class="segmented" role="group">
        <button v-for="[m, l] in DURATIONS" :key="m" type="button" :aria-pressed="sil.minutes === m" @click="sil.minutes = m">{{ l }}</button></div></div>
      <div class="form-row"><label class="lbl" for="sil-r">Reason (optional)</label><input id="sil-r" v-model="sil.reason" class="input" maxlength="300" placeholder="e.g. planned maintenance"></div>
      <p class="hint" style="margin:0">Alerts are still recorded while silenced; only messages stop. You can end a silence early under Silences.</p>
    </template>
    <template #actions><button class="btn ghost" type="button" @click="sil.open = false">Cancel</button><span style="flex:1" />
      <button class="btn primary" type="button" :disabled="sil.busy" @click="silence">Silence</button></template>
  </Modal>

  <Modal :open="edit.open" :title="edit.id ? `Edit rule ${edit.name}` : 'New alert rule'" wide @close="edit.open = false">
    <div class="form-row"><label class="lbl">Name</label><input v-model="edit.name" class="input" placeholder="e.g. Broken pods in production"></div>
    <div class="form-row"><label class="lbl">Watch for</label>
      <div class="segmented" role="group"><button v-for="(l, t) in TYPE_LABEL" v-show="(t !== 'haproxy' || canHx) && (t !== 'nginx' || canNg)" :key="t" type="button" :aria-pressed="edit.type === t" @click="edit.type = String(t)">{{ l }}</button></div></div>
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
    <template v-else-if="edit.type === 'haproxy'">
      <div class="two-col">
        <div class="form-row"><label class="lbl">Condition</label><select v-model="edit.hx_condition" class="select"
          @change="edit.threshold = HX_COND[edit.hx_condition].def ?? edit.threshold"><option v-for="(c, k) in HX_COND" :key="k" :value="k">{{ c.label }}</option></select>
          <span class="hint">{{ HX_COND[edit.hx_condition].hint }}</span></div>
        <div class="form-row"><label class="lbl">HAProxy</label><select v-model="edit.hx_instance" class="select"><option :value="null">Any HAProxy</option>
          <option v-for="i in hxInstances" :key="i.id" :value="i.id">{{ i.name }}</option></select></div>
        <div v-if="HX_COND[edit.hx_condition].unit" class="form-row"><label class="lbl">Threshold ({{ HX_COND[edit.hx_condition].unit }})</label>
          <input v-model.number="edit.threshold" class="input" type="number" min="0"></div>
      </div>
    </template>
    <template v-else-if="edit.type === 'nginx'">
      <div class="two-col">
        <div class="form-row"><label class="lbl">Condition</label><select v-model="edit.ng_condition" class="select"
          @change="edit.threshold = NG_COND[edit.ng_condition].def ?? edit.threshold"><option v-for="(c, k) in NG_COND" :key="k" :value="k">{{ c.label }}</option></select>
          <span class="hint">{{ NG_COND[edit.ng_condition].hint }}</span></div>
        <div class="form-row"><label class="lbl">Nginx</label><select v-model="edit.ng_instance" class="select"><option :value="null">Any Nginx</option>
          <option v-for="i in ngInstances" :key="i.id" :value="i.id">{{ i.name }}</option></select></div>
        <div v-if="NG_COND[edit.ng_condition].unit" class="form-row"><label class="lbl">Threshold ({{ NG_COND[edit.ng_condition].unit }})</label>
          <input v-model.number="edit.threshold" class="input" type="number" min="0"></div>
      </div>
    </template>
    <p v-else class="hint">Fires when the hub cannot reach the cluster's agent.</p>
    <div class="two-col">
      <div v-if="edit.type !== 'haproxy' && edit.type !== 'nginx'" class="form-row"><label class="lbl">Clusters</label><select v-model="edit.cluster_id" class="select"><option :value="null">All clusters</option>
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
