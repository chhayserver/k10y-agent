<script setup lang="ts">
// HAProxy: live frontends, backends and servers; instant server state changes; validated config changes; topology.
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import EmptyState from "@/components/EmptyState.vue";
import Icon from "@/components/Icon.vue";
import Skeleton from "@/components/Skeleton.vue";
import { hx, type HxBackend, type HxFrontend, type HxOp, type HxServer } from "@/lib/haproxy";
import { to } from "@/lib/nav";
import { useHaproxy } from "@/stores/haproxy";
import { useSession } from "@/stores/session";
import { openAction, type MenuItem } from "@/stores/ui";
import { useRouter } from "vue-router";
import HxBackendForm from "./HxBackendForm.vue";
import HxBackendsTable from "./HxBackendsTable.vue";
import HxFrontendsTable from "./HxFrontendsTable.vue";
import HxAi from "./HxAi.vue";
import HxCertificates from "./HxCertificates.vue";
import HxAddRoute from "./HxAddRoute.vue";
import HxCopyDialog from "./HxCopyDialog.vue";
import HxDashboard from "./HxDashboard.vue";
import HxDrift from "./HxDrift.vue";
import HxMaintenance from "./HxMaintenance.vue";
import HxWizard from "./HxWizard.vue";
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
import HxFrontendForm from "./HxFrontendForm.vue";
import HxTopology from "./HxTopology.vue";

type Tab = "overview" | "frontends" | "backends" | "topology" | "ai" | "certificates" | "sticktables" | "logs" | "history" | "config";
const props = defineProps<{ tab: Tab }>();
const s = useSession(), h = useHaproxy(), router = useRouter();
// permissions on the HAProxy that is open (roles can be given per instance)
const canOperate = computed(() => s.canHx("haproxy.operate", h.currentId));
const canConfigure = computed(() => s.canHx("haproxy.configure", h.currentId));
const canManage = computed(() => s.canHx("haproxy.manage", h.currentId));
const canAdd = computed(() => s.canAll("haproxy.manage"));

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
const frontends = computed(() => (data.value?.frontends || []).filter((f) => !f.stats_page));

// dialogs
const instDialog = reactive({ open: false, instance: null as any });
const feForm = reactive({ open: false, frontend: null as HxFrontend | null });
const beForm = reactive({ open: false, backend: null as HxBackend | null });
const srvForm = reactive({ open: false, backend: "", server: null as HxServer | null });
type Target = { id: number; name: string } | null;     // a change for another HAProxy (copy, make the group the same)
const change = reactive({ open: false, title: "", ops: [] as HxOp[], danger: false, note: "", target: null as Target });
const wizard = reactive({ open: false });
const addRoute = reactive({ open: false, frontend: null as HxFrontend | null });
const maint = reactive({ open: false, frontend: null as HxFrontend | null });
const copy = reactive({ open: false, kind: "backend" as "frontend" | "backend", item: null as HxFrontend | HxBackend | null });
const drift = reactive({ open: false });
const rolling = reactive({ open: false, backend: null as HxBackend | null });
const sync = reactive({ open: false, backend: null as HxBackend | null });
const weight = reactive({ open: false, backend: null as HxBackend | null, server: null as HxServer | null, value: 100 as number | string });
const anyDialog = computed(() => instDialog.open || feForm.open || beForm.open || srvForm.open || change.open || rolling.open || sync.open || weight.open
  || wizard.open || addRoute.open || maint.open || copy.open || drift.open);
const configRef = ref<InstanceType<typeof HxConfig> | null>(null);
const historyRef = ref<InstanceType<typeof HxHistory> | null>(null);

function review(ops: HxOp[], title: string, danger = false, note = "", target: Target = null) {
  feForm.open = beForm.open = srvForm.open = weight.open = wizard.open = addRoute.open = maint.open = copy.open = drift.open = false;
  Object.assign(change, { open: true, title, ops, danger, note, target });
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
const canAi = computed(() => s.canAll("ai.use") || s.canHx("ai.use", h.currentId));
function askAi(scope: "instance" | "frontend" | "backend", target = "") {
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
    { label: "Copy to another HAProxy…", icon: "copy", run: () => Object.assign(copy, { open: true, kind: "backend", item: b }) },
    { sep: true }, { label: "Delete backend…", icon: "trash", danger: true, disabled: b.used_by.length > 0,
      run: () => review([{ op: "delete", kind: "backend", name: b.name }], `Delete backend ${b.name}`, true) },
    ...(b.used_by.length ? [{ note: `Used by ${b.used_by.join(", ")}` }] : [])];
}
function frontendMenu(f: HxFrontend): MenuItem[] {
  const ai: MenuItem[] = canAi.value ? [{ label: "Diagnose with AI", icon: "ai", run: () => askAi("frontend", f.name) }] : [];
  if (!canConfigure.value) return ai.length ? ai : [{ note: "You can view only" }];
  const http = f.mode === "http" && !f.stats_page, inMaint = !!f.policies.maintenance?.enabled;
  return [...ai, ...(ai.length ? [{ sep: true }] : []),
    ...(http && f.simple_routes ? [{ label: "Add route…", icon: "map", run: () => Object.assign(addRoute, { open: true, frontend: f }) }] : []),
    { label: "Edit frontend…", icon: "file", run: () => Object.assign(feForm, { open: true, frontend: f }) },
    ...(http ? [inMaint
      ? { label: "Turn off maintenance page", icon: "play", run: () => review([{ op: "update", kind: "frontend", name: f.name, data: { policies: { ...f.policies, maintenance: { enabled: false } } } }], `End maintenance on ${f.name}`) }
      : { label: "Maintenance page…", icon: "pause", run: () => Object.assign(maint, { open: true, frontend: f }) }] : []),
    { label: "Copy to another HAProxy…", icon: "copy", run: () => Object.assign(copy, { open: true, kind: "frontend", item: f }) },
    { sep: true }, { label: "Delete frontend…", icon: "trash", danger: true, run: () => review([{ op: "delete", kind: "frontend", name: f.name }], `Delete frontend ${f.name}`, true) }];
}
function pick(kind: string, name: string, backend?: string) {
  focus.value = kind === "frontend" ? `f:${name}` : `b:${backend || name}`;
  router.push({ path: to(kind === "frontend" ? "haproxy-frontends" : "haproxy-backends"), query: { focus: focus.value } });
}
watch(() => router.currentRoute.value.query.focus, (f) => {
  if (typeof f !== "string") return;
  focus.value = f;
  setTimeout(() => document.getElementById(f)?.scrollIntoView({ behavior: "smooth", block: "center" }), 120);
}, { immediate: true });
</script>

<template>
  <!-- no instances yet -->
  <template v-if="instances && !instances.length">
    <section class="card"><div class="card-body">
      <EmptyState title="No HAProxy connected yet" icon="lb"
        :sub="canAdd ? 'Connect an HAProxy through its Data Plane API to monitor and manage it here.' : 'Ask an administrator to connect an HAProxy.'" />
      <div v-if="canAdd" style="display:flex;justify-content:center;margin-top:8px"><button class="btn primary" @click="Object.assign(instDialog, { open: true, instance: null })"><Icon name="lb" /><span>Add HAProxy</span></button></div>
    </div></section>
  </template>
  <template v-else>
    <div v-if="error" class="banner show"><Icon name="crit" /><span class="msg">{{ error }}</span><button class="btn" @click="refresh">Retry</button></div>
    <div v-if="tab === 'overview' && !data && error && canAdd" class="toolbar"><span class="grow" />
      <button class="btn" @click="Object.assign(instDialog, { open: true, instance: null })"><Icon name="lb" /><span>Add HAProxy</span></button></div>

    <Skeleton v-if="!data && !error && ['overview', 'frontends', 'backends', 'topology'].includes(tab)" :rows="6" />

    <!-- OVERVIEW -->
    <HxDashboard v-if="data && tab === 'overview'" :data="data" :points="h.points" :can-ai="canAi" :can-manage="canManage" :can-add="canAdd" :can-configure="canConfigure"
      @pick="pick" @ai="askAi('instance')" @go="(id) => router.push(to(id))"
      @connection="Object.assign(instDialog, { open: true, instance: data.instance })" @add-haproxy="Object.assign(instDialog, { open: true, instance: null })"
      @drift="drift.open = true" />

    <!-- FRONTENDS -->
    <HxFrontendsTable v-if="data && current && tab === 'frontends'" :key="`f${current}`" :data="data" :points="h.points" :focus="focus" :can-configure="canConfigure"
      :instance-id="current" :frontend-menu="frontendMenu" @new-frontend="wizard.open = true" @pick="pick"
      @add-route="(f) => Object.assign(addRoute, { open: true, frontend: f })" />

    <!-- BACKENDS + servers -->
    <template v-if="data && current && tab === 'backends'">
      <div v-if="!data.keeps_server_state" class="banner show info"><Icon name="warn" /><span class="msg">This HAProxy doesn't keep drain and maintenance states across reloads,
        so any config change puts drained servers back in service. Add <code>server-state-file</code> to <code>global</code> and <code>load-server-state-from-file global</code> to <code>defaults</code>, and save the state before each reload.
        <a href="https://k10y.pages.dev/docs/haproxy#keep-drain-and-maintenance-across-reloads" target="_blank" rel="noopener">How</a></span></div>
      <HxBackendsTable :key="`b${current}`" :backends="data.backends" :points="h.points" :focus="focus" :can-configure="canConfigure" :instance-id="current"
        :backend-menu="backendMenu" :server-menu="serverMenu"
        @new-backend="Object.assign(beForm, { open: true, backend: null })"
        @add-server="(b) => Object.assign(srvForm, { open: true, backend: b.name, server: null })"
        @sync="(b) => Object.assign(sync, { open: true, backend: b })" />
    </template>

    <!-- TOPOLOGY -->
    <section v-if="data && tab === 'topology'" class="card"><div class="card-body"><HxTopology :data="data" :points="h.points" @pick="pick" /></div></section>
    <HxAi v-if="current && tab === 'ai'" :key="`a${current}`" :instance-id="current" :data="data" :can-operate="canOperate" :can-configure="canConfigure"
      @review="(ops, title) => review(ops, title)" @state="aiStateChange" />
    <HxCertificates v-if="current && tab === 'certificates'" :key="`c${current}`" :instance-id="current" :can-edit="canConfigure" />
    <HxStickTables v-if="current && tab === 'sticktables'" :key="`s${current}`" :instance-id="current" :can-operate="canOperate" :can-configure="canConfigure" />
    <HxLogs v-if="current && tab === 'logs'" :key="`l${current}`" :instance-id="current" :can-edit="canConfigure" :can-operate="canOperate" @review="(ops, title) => review(ops, title)" />
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
  <HxFrontendForm v-if="current" :open="feForm.open" :frontend="feForm.frontend" :backends="data?.backends || []" :instance-id="current" @close="feForm.open = false" @review="review" />
  <HxRollingDeploy :open="rolling.open" :backend="rolling.backend" @close="rolling.open = false" />
  <HxSyncDialog v-if="current" :open="sync.open" :instance-id="current" :backend="sync.backend" @close="sync.open = false" @changed="refresh(); historyRef?.load()" />
  <Modal :open="weight.open" :title="`Weight of ${weight.backend?.name}/${weight.server?.name}`" @close="weight.open = false">
    <p class="hint" style="margin:0">A server's share of new traffic is its weight divided by the total weight of the healthy servers. Use a small weight for a canary.</p>
    <div class="form-row"><label class="lbl">Weight (0–256)</label><input v-model="weight.value" class="input" type="number" min="0" max="256">
      <input v-model="weight.value" type="range" min="0" max="256" aria-label="Weight slider"></div>
    <div class="test-result show ok"><Icon name="scale" /><span>About <b>{{ weightShare }}%</b> of new requests to {{ weight.backend?.name }} would go to {{ weight.server?.name }}.</span></div>
    <p class="hint" style="margin:0">HAProxy's Data Plane API has no live weight setting, so this is a config change with a seamless reload: it takes about two seconds and open connections are not dropped.</p>
    <template #actions><button class="btn ghost" type="button" @click="weight.open = false">Cancel</button><span style="flex:1" />
      <button class="btn primary" type="button" :disabled="Number(weight.value) === weight.server?.weight" @click="reviewWeight">Review change</button></template>
  </Modal>
  <HxBackendForm :open="beForm.open" :backend="beForm.backend" @close="beForm.open = false" @review="review" />
  <HxServerForm :open="srvForm.open" :backend="srvForm.backend" :server="srvForm.server" @close="srvForm.open = false" @review="review" />
  <HxWizard v-if="current" :open="wizard.open" :instance-id="current" :backends="data?.backends || []" :frontends="data?.frontends || []"
    @close="wizard.open = false" @review="review" @custom="wizard.open = false; Object.assign(feForm, { open: true, frontend: null })" />
  <HxAddRoute :open="addRoute.open" :frontend="addRoute.frontend" :backends="data?.backends || []" @close="addRoute.open = false" @review="review" />
  <HxMaintenance :open="maint.open" :frontend="maint.frontend" @close="maint.open = false" @review="review" />
  <HxCopyDialog v-if="current" :open="copy.open" :kind="copy.kind" :item="copy.item" :source="data" :instances="instances || []" :current-id="current"
    @close="copy.open = false" @review="(ops, title, target) => review(ops, title, false, '', target)" />
  <HxDrift v-if="current" :open="drift.open" :instance-id="current" :instance-name="h.current?.name || ''" :can-configure="canConfigure"
    @close="drift.open = false" @review="(ops, title, target) => review(ops, title, true, '', target)" />
  <HxChangeDialog v-if="current" :open="change.open" :instance-id="change.target?.id || current" :instance-name="change.target?.name || h.current?.name"
    :group="change.target ? [] : data?.group" :note="change.note"
    :title="change.title" :ops="change.ops" :danger="change.danger"
    @close="change.open = false" @done="changed" />
</template>
