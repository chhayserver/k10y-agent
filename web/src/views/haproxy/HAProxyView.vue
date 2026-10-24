<script setup lang="ts">
// HAProxy: live services, backends and servers; instant server state changes; validated config changes; topology.
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import Badge from "@/components/Badge.vue";
import EmptyState from "@/components/EmptyState.vue";
import Icon from "@/components/Icon.vue";
import RowMenu from "@/components/RowMenu.vue";
import Skeleton from "@/components/Skeleton.vue";
import { backendHealth, bindText, hx, num, ROUTE_KIND, routeText, STATE, type HxBackend, type HxFrontend, type HxOp, type HxOverview, type HxServer } from "@/lib/haproxy";
import { fmt } from "@/lib/format";
import { to } from "@/lib/nav";
import { useHaproxy } from "@/stores/haproxy";
import { useSession } from "@/stores/session";
import { openAction, type MenuItem } from "@/stores/ui";
import { useRouter } from "vue-router";
import HxBackendForm from "./HxBackendForm.vue";
import HxAi from "./HxAi.vue";
import HxCertificates from "./HxCertificates.vue";
import HxCharts from "./HxCharts.vue";
import HxHistory from "./HxHistory.vue";
import HxLogs from "./HxLogs.vue";
import HxRollingDeploy from "./HxRollingDeploy.vue";
import HxStickTables from "./HxStickTables.vue";
import HxSyncDialog from "./HxSyncDialog.vue";
import Modal from "@/components/Modal.vue";
import HxChangeDialog from "./HxChangeDialog.vue";
import HxConfig from "./HxConfig.vue";
import HxInstanceDialog from "./HxInstanceDialog.vue";
import HxServerForm from "./HxServerForm.vue";
import HxServiceForm from "./HxServiceForm.vue";
import HxTopology from "./HxTopology.vue";

type Tab = "overview" | "services" | "backends" | "topology" | "ai" | "certificates" | "sticktables" | "logs" | "history" | "config";
const props = defineProps<{ tab: Tab }>();
const s = useSession(), h = useHaproxy(), router = useRouter();
const canOperate = computed(() => s.canGlobal("haproxy.operate"));
const canConfigure = computed(() => s.canGlobal("haproxy.configure"));
const canManage = computed(() => s.canGlobal("clusters.manage"));

const instances = computed(() => h.instances), current = computed(() => h.currentId);
const data = computed(() => h.data), error = computed(() => h.error);
const focus = ref("");   // item to highlight after a click in the topology or problems list
const refresh = () => h.refresh();
let timer = 0;
onMounted(async () => {
  if (h.instances == null) await h.loadInstances(); else if (!h.data) h.refresh();
  timer = window.setInterval(() => { if (!h.paused && !document.hidden && !anyDialog.value) h.refresh(); }, 5000);
});
onBeforeUnmount(() => clearInterval(timer));

// summary numbers
const services = computed(() => (data.value?.frontends || []).filter((f) => !f.stats_page));
const allServers = computed(() => (data.value?.backends || []).flatMap((b) => b.servers.map((sv) => ({ ...sv, backend: b.name }))));
const problems = computed(() => {
  const out: { level: "critical" | "warning"; text: string; backend: string }[] = [];
  for (const b of data.value?.backends || []) {
    const h = backendHealth(b);
    if (h.total && h.up === 0) out.push({ level: "critical", text: `Backend ${b.name} has no server up${b.used_by.length ? ` (used by ${b.used_by.join(", ")})` : ""}`, backend: b.name });
    for (const sv of b.servers) if (sv.state === "down" && !(h.total && h.up === 0))
      out.push({ level: "warning", text: `${b.name}/${sv.name} is down: ${sv.stats.check_desc || sv.stats.check_status || "health check failing"}`, backend: b.name });
  }
  return out;
});
const totals = computed(() => {
  const fe = services.value;
  return { sessions: fe.reduce((a, f) => a + (Number(f.stats.scur) || 0), 0), rate: fe.reduce((a, f) => a + (Number(f.stats.req_rate ?? f.stats.rate) || 0), 0),
    e5: fe.reduce((a, f) => a + (Number(f.stats.hrsp_5xx) || 0), 0), up: allServers.value.filter((x) => x.state === "up" || x.state === "nocheck").length,
    down: allServers.value.filter((x) => x.state === "down").length, out: allServers.value.filter((x) => x.state === "drain" || x.state === "maint").length };
});

// dialogs
const instDialog = reactive({ open: false, instance: null as any });
const svcForm = reactive({ open: false, frontend: null as HxFrontend | null });
const beForm = reactive({ open: false, backend: null as HxBackend | null });
const srvForm = reactive({ open: false, backend: "", server: null as HxServer | null });
const change = reactive({ open: false, title: "", ops: [] as HxOp[], danger: false, note: "" });
const rolling = reactive({ open: false, backend: null as HxBackend | null });
const sync = reactive({ open: false, backend: null as HxBackend | null });
const weight = reactive({ open: false, backend: null as HxBackend | null, server: null as HxServer | null, value: 100 as number | string });
const anyDialog = computed(() => instDialog.open || svcForm.open || beForm.open || srvForm.open || change.open || rolling.open || sync.open || weight.open);
const configRef = ref<InstanceType<typeof HxConfig> | null>(null);
const historyRef = ref<InstanceType<typeof HxHistory> | null>(null);

function review(ops: HxOp[], title: string, danger = false, note = "") {
  svcForm.open = beForm.open = srvForm.open = weight.open = false;
  Object.assign(change, { open: true, title, ops, danger, note });
}
async function changed() { change.open = false; await refresh(); configRef.value?.load(); historyRef.value?.load(); }

// Weight: share of traffic. The Data Plane API can't change it at runtime, so it's a quick config change.
const weightShare = computed(() => {
  const b = weight.backend, w = Number(weight.value) || 0;
  if (!b) return 0;
  const others = b.servers.filter((x) => x.name !== weight.server?.name && !x.backup && (x.state === "up" || x.state === "nocheck")).reduce((a, x) => a + (x.weight || 0), 0);
  return w + others ? Math.round((100 * w) / (w + others)) : 0;
});
function reviewWeight() {
  review([{ op: "update", kind: "server", backend: weight.backend!.name, name: weight.server!.name, data: { weight: Number(weight.value) } }],
    `Weight of ${weight.backend!.name}/${weight.server!.name}: ${weight.server!.weight} → ${weight.value}`);
}

function setState(b: string, sv: HxServer, state: "ready" | "drain" | "maint") {
  const what = { ready: "Put back in service", drain: "Drain", maint: "Maintenance" }[state];
  const desc = { ready: "The server takes new traffic again (if its health check passes).",
    drain: "No new connections; existing sessions finish normally. Good before a deploy.",
    maint: "The server is taken out of rotation now, and its health checks stop." }[state];
  const keep = data.value?.keeps_server_state ? "" : state === "ready" ? ""
    : " <b>Note:</b> this HAProxy has no server-state file, so the next config change will put the server back in service.";
  openAction({ title: `${what}: ${b}/${sv.name}`, desc: `${desc} This is applied instantly, without reloading HAProxy.${keep}`, confirmLabel: what, noDryRun: true,
    run: async () => { const r = await hx.setState(current.value!, b, sv.name, state); await refresh(); return r.message; } });
}
function serverMenu(b: HxBackend, sv: HxServer): MenuItem[] {
  const items: MenuItem[] = [];
  if (canOperate.value) {
    if (sv.state !== "up" && sv.state !== "nocheck") items.push({ label: "Put back in service", icon: "play", run: () => setState(b.name, sv, "ready") });
    if (sv.state !== "drain") items.push({ label: "Drain", icon: "drain", run: () => setState(b.name, sv, "drain") });
    if (sv.state !== "maint") items.push({ label: "Maintenance", icon: "pause", run: () => setState(b.name, sv, "maint") });
  }
  if (canConfigure.value) {
    items.push({ sep: true }, { label: "Change weight…", icon: "scale", run: () => Object.assign(weight, { open: true, backend: b, server: sv, value: sv.weight }) },
      { label: "Edit…", icon: "file", run: () => Object.assign(srvForm, { open: true, backend: b.name, server: sv }) },
      { label: "Remove server…", icon: "trash", danger: true, run: () => review([{ op: "delete", kind: "server", backend: b.name, name: sv.name }], `Remove server ${b.name}/${sv.name}`, true) });
  }
  return items.length ? items : [{ note: "You can view only" }];
}
const canAi = computed(() => s.canGlobal("ai.use"));
function askAi(scope: "instance" | "service" | "backend", target = "") {
  router.push({ path: to("haproxy-ai"), query: { scope, target, run: "1" } });
}
function aiStateChange(backend: string, server: string, state: "ready" | "drain" | "maint") {
  const b = data.value?.backends.find((x) => x.name === backend), sv = b?.servers.find((x) => x.name === server);
  if (b && sv) setState(b.name, sv, state);
}
function backendMenu(b: HxBackend): MenuItem[] {
  const ops: MenuItem[] = canOperate.value && b.servers.filter((x) => !x.backup).length > 1
    ? [{ label: "Rolling deploy…", icon: "refresh", run: () => Object.assign(rolling, { open: true, backend: b }) }] : [];
  if (canAi.value) ops.push({ label: "Diagnose with AI", icon: "ai", run: () => askAi("backend", b.name) });
  if (!canConfigure.value) return ops.length ? ops : [{ note: "You can view only" }];
  return [...ops, ...(ops.length ? [{ sep: true }] : []), { label: "Add server…", icon: "upload", run: () => Object.assign(srvForm, { open: true, backend: b.name, server: null }) },
    { label: "Edit backend…", icon: "file", run: () => Object.assign(beForm, { open: true, backend: b }) },
    { label: b.k8s_sync ? "Kubernetes sync…" : "Sync from Kubernetes…", icon: "logo", run: () => Object.assign(sync, { open: true, backend: b }) },
    { sep: true }, { label: "Delete backend…", icon: "trash", danger: true, disabled: b.used_by.length > 0,
      run: () => review([{ op: "delete", kind: "backend", name: b.name }], `Delete backend ${b.name}`, true) },
    ...(b.used_by.length ? [{ note: `Used by ${b.used_by.join(", ")}` }] : [])];
}
function serviceMenu(f: HxFrontend): MenuItem[] {
  const ai: MenuItem[] = canAi.value ? [{ label: "Diagnose with AI", icon: "ai", run: () => askAi("service", f.name) }] : [];
  if (!canConfigure.value) return ai.length ? ai : [{ note: "You can view only" }];
  return [...ai, { label: "Edit service…", icon: "file", run: () => Object.assign(svcForm, { open: true, frontend: f }) },
    { sep: true }, { label: "Delete service…", icon: "trash", danger: true, run: () => review([{ op: "delete", kind: "frontend", name: f.name }], `Delete service ${f.name}`, true) }];
}
function pick(kind: string, name: string, backend?: string) {
  focus.value = kind === "frontend" ? `f:${name}` : `b:${backend || name}`;
  router.push({ path: to(kind === "frontend" ? "haproxy-services" : "haproxy-backends"), query: { focus: focus.value } });
}
watch(() => router.currentRoute.value.query.focus, (f) => {
  if (typeof f !== "string") return;
  focus.value = f;
  setTimeout(() => document.getElementById(f)?.scrollIntoView({ behavior: "smooth", block: "center" }), 120);
}, { immediate: true });
const rate = (st: any) => num(st.req_rate ?? st.rate);
</script>

<template>
  <!-- no instances yet -->
  <template v-if="instances && !instances.length">
    <section class="card"><div class="card-body">
      <EmptyState title="No HAProxy connected yet" icon="lb"
        :sub="canManage ? 'Connect an HAProxy through its Data Plane API to monitor and manage it here.' : 'Ask an administrator to connect an HAProxy.'" />
      <div v-if="canManage" style="display:flex;justify-content:center;margin-top:8px"><button class="btn primary" @click="Object.assign(instDialog, { open: true, instance: null })"><Icon name="lb" /><span>Add HAProxy</span></button></div>
    </div></section>
  </template>
  <template v-else>
    <div v-if="error" class="banner show"><Icon name="crit" /><span class="msg">{{ error }}</span><button class="btn" @click="refresh">Retry</button></div>
    <div v-if="tab === 'overview'" class="toolbar">
      <span v-if="data" class="hint">{{ data.instance.url }} · config version {{ data.config_version }}</span><span class="grow" />
      <button v-if="canManage && data" class="btn" @click="Object.assign(instDialog, { open: true, instance: data.instance })"><Icon name="gear" /><span>Connection</span></button>
      <button v-if="canManage" class="btn" @click="Object.assign(instDialog, { open: true, instance: null })"><Icon name="lb" /><span>Add HAProxy</span></button>
    </div>

    <Skeleton v-if="!data && !error && ['overview', 'services', 'backends', 'topology'].includes(tab)" :rows="6" />

    <!-- OVERVIEW -->
    <template v-if="data && tab === 'overview'">
      <div class="hx-cards">
        <div class="card stat"><div class="lbl">Services</div><b>{{ services.length }}</b><span class="hint">{{ data.backends.length }} backends</span></div>
        <div class="card stat"><div class="lbl">Servers up</div><b>{{ totals.up }} <small>/ {{ allServers.length }}</small></b>
          <span class="hint">{{ totals.down }} down · {{ totals.out }} drained or in maintenance</span></div>
        <div class="card stat"><div class="lbl">Current sessions</div><b>{{ num(totals.sessions) }}</b><span class="hint">across all services</span></div>
        <div class="card stat"><div class="lbl">Requests / s</div><b>{{ num(totals.rate) }}</b><span class="hint">{{ num(totals.e5) }} server errors (5xx) since start</span></div>
      </div>
      <HxCharts :points="h.points" :services="services.map((f) => f.name)" :backends="data.backends.map((b) => b.name)" style="margin-top:16px" />
      <section class="card" style="margin-top:16px"><div class="card-head"><h2>Problems</h2>
        <div v-if="canAi" class="right"><button class="link" @click="askAi('instance')">Ask AI</button></div></div><div class="card-body">
        <EmptyState v-if="!problems.length" title="All clear" sub="Every backend has healthy servers." icon="ok" good />
        <ul v-else class="list"><li v-for="p in problems" :key="p.text" class="clickable" @click="pick('backend', p.backend)">
          <span class="ic"><Icon :name="p.level === 'critical' ? 'crit' : 'warn'" :style="{ color: p.level === 'critical' ? 'var(--critical)' : 'var(--warning)' }" /></span>
          <div><div class="main-line">{{ p.text }}</div></div></li></ul>
      </div></section>
      <section class="card" style="margin-top:16px"><div class="card-head"><h2>Topology</h2><div class="right"><button class="btn sm" @click="router.push(to('haproxy-topology'))">Full view</button></div></div>
        <div class="card-body"><HxTopology :data="data" @pick="pick" /></div></section>
    </template>

    <!-- SERVICES (frontends) -->
    <template v-if="data && tab === 'services'">
      <div class="toolbar"><span class="hint">Services are HAProxy <b>frontends</b>: where clients connect, and which backend gets each request.</span><span class="grow" />
        <button v-if="canConfigure" class="btn primary" @click="Object.assign(svcForm, { open: true, frontend: null })"><Icon name="upload" /><span>New service</span></button></div>
      <section class="card flush"><div class="card-body table-wrap">
        <table class="data"><thead><tr><th>Service</th><th>Status</th><th>Listen</th><th>Routes</th><th class="num">Sessions</th><th class="num">Req/s</th><th class="num">5xx</th><th class="actions"><span class="sr-only">Actions</span></th></tr></thead>
          <tbody><tr v-for="f in data.frontends" :id="`f:${f.name}`" :key="f.name" :class="{ 'hx-focus': focus === `f:${f.name}` }">
            <td class="primary"><span class="mono">{{ f.name }}</span> <span class="chip">{{ f.mode.toUpperCase() }}</span><span v-if="f.stats_page" class="chip">stats page</span></td>
            <td><Badge :level="f.stats.status === 'OPEN' ? 'good' : 'critical'" :label="f.stats.status === 'OPEN' ? 'Open' : (f.stats.status || 'Unknown')" /></td>
            <td class="mono">{{ f.binds.map(bindText).join(", ") }}
              <div class="hx-policies"><span v-if="f.policies.redirect_https" class="chip">→ HTTPS</span>
                <span v-if="f.policies.allow_ips.length" class="chip" :title="f.policies.allow_ips.join(' ')">IP allow-list ({{ f.policies.allow_ips.length }})</span>
                <span v-if="f.policies.rate_limit" class="chip">≤ {{ f.policies.rate_limit.requests }} req / {{ f.policies.rate_limit.period }} s per IP</span></div></td>
            <td class="wrap"><div class="hx-routes">
              <div v-for="(r, i) in f.routes" :key="i"><span class="dim">{{ ROUTE_KIND[r.kind] }}</span> <code>{{ routeText(r) }}</code> → <button class="link" @click="pick('backend', r.backend)">{{ r.backend }}</button></div>
              <div v-if="f.default_backend"><span class="dim">Everything else</span> → <button class="link" @click="pick('backend', f.default_backend)">{{ f.default_backend }}</button></div>
              <span v-if="!f.routes.length && !f.default_backend" class="dim">{{ f.stats_page ? "HAProxy statistics page" : "No backend" }}</span></div></td>
            <td class="num">{{ num(f.stats.scur) }}</td><td class="num">{{ rate(f.stats) }}</td>
            <td class="num"><b v-if="Number(f.stats.hrsp_5xx)" style="color:var(--critical)">{{ num(f.stats.hrsp_5xx) }}</b><span v-else>0</span></td>
            <td class="actions"><RowMenu :items="() => serviceMenu(f)" :label="f.name" /></td></tr></tbody></table>
      </div></section>
    </template>

    <!-- BACKENDS + servers -->
    <template v-if="data && tab === 'backends'">
      <div class="toolbar"><span class="hint"><b>Drain</b> and <b>Maintenance</b> apply instantly without a reload. Adding, editing and removing are validated by HAProxy before they apply.</span><span class="grow" />
        <button v-if="canConfigure" class="btn primary" @click="Object.assign(beForm, { open: true, backend: null })"><Icon name="upload" /><span>New backend</span></button></div>
      <div v-if="!data.keeps_server_state" class="banner show info"><Icon name="warn" /><span class="msg">This HAProxy doesn't keep drain and maintenance states across reloads,
        so any config change puts drained servers back in service. Add <code>server-state-file</code> to <code>global</code> and <code>load-server-state-from-file global</code> to <code>defaults</code>, and save the state before each reload.
        <a href="https://k10y.pages.dev/docs/haproxy#keep-drain-and-maintenance-across-reloads" target="_blank" rel="noopener">How</a></span></div>
      <section v-for="b in data.backends" :id="`b:${b.name}`" :key="b.name" class="card flush hx-backend" :class="{ 'hx-focus': focus === `b:${b.name}` }">
        <div class="card-head"><h2 class="mono" style="text-transform:none;letter-spacing:0">{{ b.name }}</h2>
          <button v-if="b.k8s_sync" class="chip hx-sync" :class="{ err: b.k8s_sync.last_error }" :title="b.k8s_sync.last_error || b.k8s_sync.last_result" @click="Object.assign(sync, { open: true, backend: b })">
            <Icon name="logo" /> {{ b.k8s_sync.namespace }}/{{ b.k8s_sync.service }}{{ b.k8s_sync.enabled ? "" : " (paused)" }}</button>
          <Badge :level="backendHealth(b).level" :label="backendHealth(b).label" />
          <span class="hint">{{ b.balance }} · {{ b.mode.toUpperCase() }} · check: {{ b.check.type === "http" ? `GET ${b.check.uri}` : b.check.type === "tcp" ? "TCP" : "connect" }}
            <template v-if="b.used_by.length"> · used by {{ b.used_by.join(", ") }}</template><template v-else> · not used by any service</template></span>
          <div class="right"><RowMenu :items="() => backendMenu(b)" :label="b.name" /></div></div>
        <div class="card-body table-wrap">
          <table class="data"><thead><tr><th>Server</th><th>State</th><th>Address</th><th class="num">Weight</th><th>Health check</th><th class="num">Sessions</th><th class="num">Req total</th><th class="num">5xx</th><th>Since</th><th class="actions"><span class="sr-only">Actions</span></th></tr></thead>
            <tbody>
              <tr v-if="!b.servers.length"><td colspan="10" class="dim">No servers yet.<button v-if="canConfigure" class="link" style="margin-left:8px" @click="Object.assign(srvForm, { open: true, backend: b.name, server: null })">Add one</button></td></tr>
              <tr v-for="sv in b.servers" :key="sv.name">
                <td class="primary mono">{{ sv.name }}<span v-if="sv.backup" class="chip">backup</span></td>
                <td><Badge :level="STATE[sv.state].level" :label="STATE[sv.state].label" /></td>
                <td class="mono">{{ sv.address }}:{{ sv.port }}</td>
                <td class="num">{{ sv.weight }}</td>
                <td><span v-if="!sv.check" class="dim">off</span><span v-else :title="sv.stats.check_desc"><code>{{ (sv.stats.check_status || "").replace("* ", "") || "–" }}</code>
                  <span v-if="sv.stats.check_duration != null" class="dim" style="margin-left:6px">{{ sv.stats.check_duration }} ms</span></span></td>
                <td class="num">{{ num(sv.stats.scur) }}</td><td class="num">{{ num(sv.stats.stot) }}</td>
                <td class="num"><b v-if="Number(sv.stats.hrsp_5xx)" style="color:var(--critical)">{{ num(sv.stats.hrsp_5xx) }}</b><span v-else>0</span></td>
                <td class="dim">{{ sv.stats.lastchg != null ? fmt.age(Number(sv.stats.lastchg)) : "–" }}</td>
                <td class="actions"><RowMenu :items="() => serverMenu(b, sv)" :label="sv.name" /></td></tr>
            </tbody></table></div>
      </section>
    </template>

    <!-- TOPOLOGY -->
    <section v-if="data && tab === 'topology'" class="card"><div class="card-body"><HxTopology :data="data" @pick="pick" /></div></section>
    <HxAi v-if="current && tab === 'ai'" :key="`a${current}`" :instance-id="current" :data="data" :can-operate="canOperate" :can-configure="canConfigure"
      @review="(ops, title) => review(ops, title)" @state="aiStateChange" />
    <HxCertificates v-if="current && tab === 'certificates'" :key="`c${current}`" :instance-id="current" :can-edit="canConfigure" />
    <HxStickTables v-if="current && tab === 'sticktables'" :key="`s${current}`" :instance-id="current" />
    <HxLogs v-if="current && tab === 'logs'" :key="`l${current}`" :instance-id="current" :can-edit="canConfigure" @review="(ops, title) => review(ops, title)" />
    <HxHistory v-if="current && tab === 'history'" ref="historyRef" :key="`h${current}`" :instance-id="current" :can-edit="canConfigure"
      @review="(ops, title, note) => review(ops, title, false, note)" />

    <!-- CONFIG -->
    <section v-if="current && tab === 'config'" class="card"><div class="card-body">
      <HxConfig v-if="canConfigure" ref="configRef" :key="current" :instance-id="current" :can-edit="canConfigure"
        @review="(ops, title) => review(ops, title)" />
      <EmptyState v-else title="Config needs the haproxy.configure permission" icon="lock" sub="The raw configuration can contain passwords, so it's only shown to people who can change it." />
    </div></section>
  </template>

  <HxInstanceDialog :open="instDialog.open" :instance="instDialog.instance" @close="instDialog.open = false"
    @saved="async (id) => { instDialog.open = false; await h.loadInstances(id); h.refresh(); }" />
  <HxServiceForm v-if="current" :open="svcForm.open" :frontend="svcForm.frontend" :backends="data?.backends || []" :instance-id="current" @close="svcForm.open = false" @review="review" />
  <HxRollingDeploy :open="rolling.open" :backend="rolling.backend" @close="rolling.open = false" />
  <HxSyncDialog v-if="current" :open="sync.open" :instance-id="current" :backend="sync.backend" @close="sync.open = false" @changed="refresh(); historyRef?.load()" />
  <Modal :open="weight.open" :title="`Weight of ${weight.backend?.name}/${weight.server?.name}`" @close="weight.open = false">
    <p class="hint" style="margin:0">A server's share of new traffic is its weight divided by the total weight of the healthy servers. Use a small weight for a canary.</p>
    <div class="form-row"><label class="lbl">Weight (0–256)</label><input v-model="weight.value" class="input" type="number" min="0" max="256">
      <input v-model="weight.value" type="range" min="0" max="256" aria-label="Weight slider"></div>
    <div class="test-result show ok"><Icon name="scale" /><span>About <b>{{ weightShare }}%</b> of new requests to {{ weight.backend?.name }} would go to {{ weight.server?.name }}.</span></div>
    <p class="hint" style="margin:0">The Data Plane API can't change weights at runtime, so this is applied as a config change with a graceful reload (about two seconds, no dropped connections).</p>
    <template #actions><button class="btn ghost" type="button" @click="weight.open = false">Cancel</button><span style="flex:1" />
      <button class="btn primary" type="button" :disabled="Number(weight.value) === weight.server?.weight" @click="reviewWeight">Review change</button></template>
  </Modal>
  <HxBackendForm :open="beForm.open" :backend="beForm.backend" @close="beForm.open = false" @review="review" />
  <HxServerForm :open="srvForm.open" :backend="srvForm.backend" :server="srvForm.server" @close="srvForm.open = false" @review="review" />
  <HxChangeDialog v-if="current" :open="change.open" :instance-id="current" :instance-name="h.current?.name" :group="data?.group" :note="change.note"
    :title="change.title" :ops="change.ops" :danger="change.danger"
    @close="change.open = false" @done="changed" />
</template>
