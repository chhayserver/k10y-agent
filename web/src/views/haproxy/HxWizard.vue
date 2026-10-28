<script setup lang="ts">
// New frontend, the guided way: choose what you are setting up (microservices by path, a web app over HTTPS,
// RabbitMQ, a database…), fill in only what that setup needs (examples are pre-filled in grey), and get one
// reviewed change that creates the frontend and every backend with its servers, written the house way.
import { computed, reactive, ref, watch } from "vue";
import Icon from "@/components/Icon.vue";
import Modal from "@/components/Modal.vue";
import { hx, type HxBackend, type HxFrontend, type HxOp } from "@/lib/haproxy";
import { CERT_FILE_EXAMPLE, USE_CASES, backendChanges, backendData, serverName, splitHostPort, timingData, type Settings, type UseCase } from "@/lib/haproxyCatalog";
import HxBackendSettings from "./HxBackendSettings.vue";

const props = defineProps<{ open: boolean; instanceId: number; backends: HxBackend[]; frontends: HxFrontend[] }>();
const emit = defineEmits<{ close: []; review: [ops: HxOp[], title: string]; custom: [] }>();

interface Row { address: string; backup: boolean }
interface Target { kind: string; path: string; backend: string; check: string; servers: Row[]; ex: UseCase["backends"][number] }
const step = ref<"pick" | "details">("pick"), uc = ref<UseCase>(USE_CASES[0]), err = ref("");
const certs = ref<any[]>([]);
const f = reactive({ name: "", address: "", port: "" as number | string, tls: false, certMode: "file", certFile: "", cert: "", redirect: true, also80: false,
  forwardedPort: true, tlsToServers: false, everythingElse: true, routes: [] as Target[], main: null as Target | null, settings: backendData(USE_CASES[0]) as Settings });

const GROUPS: [string, string][] = [["web", "Web and APIs (HTTP)"], ["data", "Message queues and databases (TCP)"], ["other", "Other"]];
const MATCH: [string, string][] = [["service", "/path and /path/…"], ["path_prefix", "starts with"], ["path", "is exactly"]];
const target = (ex: UseCase["backends"][number]): Target => ({ kind: "service", path: "", backend: "", check: "", servers: ex.servers.map((x) => ({ address: "", backup: !!x.backup })), ex });

function pick(u: UseCase) {
  uc.value = u; err.value = "";
  Object.assign(f, { name: "", address: "", port: "", tls: !!u.tls, certMode: certs.value.length && !u.tls ? "uploaded" : "file", certFile: "", cert: certs.value[0]?.name || "",
    redirect: u.redirect ?? true, also80: false, forwardedPort: u.forwardedPort ?? u.mode === "http", tlsToServers: !!u.tlsToServers,
    everythingElse: u.everythingElse ?? true, settings: backendData(u) });
  if (u.shape === "paths") { f.routes = u.backends.filter((b) => b.path).map(target); f.main = target(u.backends.find((b) => !b.path)!); }
  else { f.routes = []; f.main = target(u.backends[0]); }
  step.value = "details";
}
watch(() => props.open, async (o) => {
  if (!o) return;
  step.value = "pick"; err.value = "";
  try { certs.value = await hx.certificates(props.instanceId); } catch { certs.value = []; }
});

const http = computed(() => uc.value.mode === "http");
const port = computed(() => Number(f.port) || uc.value.port);
const usedPorts = computed(() => new Set(props.frontends.flatMap((x) => x.binds.map((b) => Number(b.port)))));
const ports = computed(() => [port.value, ...(http.value && f.tls && f.also80 ? [80] : [])]);
const clash = computed(() => ports.value.filter((p) => usedPorts.value.has(p)));
const allTargets = computed(() => [...f.routes, ...(f.main && (uc.value.shape === "proxy" || f.everythingElse) ? [f.main] : [])]);
const nameOf = (t: Target) => (t.backend.trim() || t.ex.name);
const srvName = (t: Target, i: number) => serverName(uc.value.serverName, nameOf(t), i);
const pathOf = (t: Target) => (t.path.trim() || t.ex.path || "").replace(/\/+$/, "") || "/";

function submit() {
  err.value = "";
  const fname = (f.name.trim() || uc.value.frontend), address = f.address.trim() || "0.0.0.0";
  const existingB = new Set(props.backends.map((b) => b.name)), existingF = new Set(props.frontends.map((x) => x.name));
  if (existingF.has(fname)) { err.value = `A frontend named ${fname} already exists. Choose another name.`; return; }
  if (clash.value.length) { err.value = `Port ${clash.value.join(", ")} is already used by another frontend.`; return; }
  const certificate = f.certMode === "file" ? (f.certFile.trim() || CERT_FILE_EXAMPLE) : f.cert;
  if (http.value && f.tls && !certificate) { err.value = "Choose a certificate for HTTPS."; return; }
  if (http.value && f.tls && f.certMode === "file" && !certificate.startsWith("/")) { err.value = `The certificate file is a full path on the HAProxy server, e.g. ${CERT_FILE_EXAMPLE}`; return; }
  const ops: HxOp[] = [], names = new Set<string>();
  for (const t of allTargets.value) {
    const n = nameOf(t);
    if (existingB.has(n) || names.has(n)) { err.value = `Backend name ${n} is already used. Give each backend its own name.`; return; }
    if (f.routes.includes(t) && !pathOf(t).startsWith("/")) { err.value = `The path for ${n} must start with /, e.g. /${n.replace(/^inbound-/, "")}`; return; }
    names.add(n);
    const checkPath = t.check.trim() || t.ex.check;
    const s: Settings = http.value && checkPath ? { ...f.settings, check: { type: "http", method: "GET", uri: checkPath, host: "" } } : f.settings;
    ops.push({ op: "create", kind: "backend", name: n, data: backendChanges(s, null) });
    const rows = t.servers.map((r, i) => ({ ...r, address: r.address.trim() || t.ex.servers[i]?.address || "" })).filter((r) => r.address);
    if (!rows.length) { err.value = `Add at least one server to ${n}.`; return; }
    for (const [i, r] of rows.entries()) {
      const hp = splitHostPort(r.address);
      if (!hp) { err.value = `${n}, server ${i + 1}: write it as address:port, e.g. 172.2.2.1:32133`; return; }
      ops.push({ op: "create", kind: "server", backend: n, name: srvName(t, i),
        data: { ...hp, check: true, backup: r.backup, ...timingData(s), ...(f.tlsToServers ? { ssl: true } : {}) } });
    }
  }
  const routes = f.routes.map((t) => ({ kind: t.kind, value: pathOf(t), backend: nameOf(t) }))
    .sort((a, b) => b.value.length - a.value.length);     // longer paths first, so /order/admin wins over /order
  const binds = http.value && f.tls
    ? [{ address, port: port.value, ssl: true, certificate }, ...(f.also80 ? [{ address, port: 80 }] : [])]
    : [{ address, port: port.value }];
  const fe: Record<string, any> = { mode: uc.value.mode, binds, default_backend: f.main && (uc.value.shape === "proxy" || f.everythingElse) ? nameOf(f.main) : null,
    routes: uc.value.shape === "paths" ? routes : [], timeouts: { client: uc.value.timeouts?.client || 0 } };
  if (http.value) fe.policies = { allow_ips: [], redirect_https: f.tls && f.redirect, rate_limit: null, forwarded_port: f.forwardedPort };
  ops.push({ op: "create", kind: "frontend", name: fname, data: fe });
  emit("review", ops, `New ${uc.value.label.toLowerCase()}: frontend ${fname} with ${names.size} backend${names.size > 1 ? "s" : ""}`);
}
</script>

<template>
  <Modal :open="open" :title="step === 'pick' ? 'New frontend' : uc.label" wide @close="emit('close')">
    <!-- step 1: what are you setting up? -->
    <template v-if="step === 'pick'">
      <p class="hint" style="margin:0">What are you setting up? k10y fills in the protocol, balancing, health checks and timeouts the usual way, with example values you can keep or change.</p>
      <section v-for="[g, title] in GROUPS" :key="g" class="hxw-group">
        <h3>{{ title }}</h3>
        <div class="hxw-cards">
          <button v-for="u in USE_CASES.filter((x) => x.group === g)" :key="u.id" type="button" class="hxw-card" @click="pick(u)">
            <Icon :name="u.icon" /><span><b>{{ u.label }}</b><span class="hint">{{ u.desc }}</span></span></button></div>
      </section>
      <button class="link" type="button" style="align-self:flex-start" @click="emit('custom')">Start from a blank form instead</button>
    </template>

    <!-- step 2: only what this setup needs -->
    <template v-else>
      <div class="hxw-top"><span class="chip">{{ uc.mode.toUpperCase() }}</span><span class="hint">{{ uc.desc }}</span><span class="grow" />
        <button class="link" type="button" @click="step = 'pick'">Change type</button></div>
      <div v-if="uc.note" class="test-result show"><Icon name="tag" /><span>{{ uc.note }}</span></div>

      <div class="hxw-row">
        <div class="form-row"><label class="lbl" for="hxw-name">Frontend name</label>
          <input id="hxw-name" v-model="f.name" class="input mono" :placeholder="uc.frontend"></div>
        <div class="form-row" style="max-width:180px"><label class="lbl" for="hxw-addr">Listen address</label>
          <input id="hxw-addr" v-model="f.address" class="input mono" placeholder="0.0.0.0"></div>
        <div class="form-row" style="max-width:140px"><label class="lbl" for="hxw-port">Port</label>
          <input id="hxw-port" v-model="f.port" class="input" type="number" min="1" max="65535" :placeholder="String(uc.port)"></div>
      </div>

      <div v-if="http" class="hxw-box">
        <label class="pill" style="align-self:flex-start"><input v-model="f.tls" type="checkbox"> HTTPS (TLS on this port)</label>
        <template v-if="f.tls">
          <div class="hxw-row" style="align-items:center">
            <div class="segmented" role="group" aria-label="Certificate">
              <button type="button" :aria-pressed="f.certMode === 'file'" @click="f.certMode = 'file'">File on the HAProxy</button>
              <button type="button" :aria-pressed="f.certMode === 'uploaded'" :disabled="!certs.length" @click="f.certMode = 'uploaded'">Uploaded certificate</button></div>
            <input v-if="f.certMode === 'file'" v-model="f.certFile" class="input mono" :placeholder="CERT_FILE_EXAMPLE" aria-label="Certificate file" style="flex:1;min-width:220px">
            <select v-else v-model="f.cert" class="select" style="max-width:260px" aria-label="Uploaded certificate">
              <option v-for="c in certs" :key="c.name" :value="c.name">{{ c.name }}</option></select>
          </div>
          <div class="hxw-checks">
            <label class="pill"><input v-model="f.redirect" type="checkbox"> Redirect plain HTTP to HTTPS</label>
            <label class="pill"><input v-model="f.also80" type="checkbox"> Also listen on port 80</label></div>
        </template>
        <div class="hxw-checks">
          <label class="pill"><input v-model="f.forwardedPort" type="checkbox"> Send <code>X-Forwarded-Port</code> to the servers</label>
          <label class="pill"><input v-model="f.tlsToServers" type="checkbox"> Servers speak TLS (<code>ssl verify none</code>)</label></div>
      </div>
      <span v-if="clash.length" class="hint" style="color:var(--critical)">Port {{ clash.join(", ") }} is already used by another frontend on this HAProxy.</span>

      <!-- microservices: one row per path -->
      <div v-if="uc.shape === 'paths'" class="form-row"><label class="lbl">Routes</label>
        <div class="hxw-routes">
          <div class="hxw-rhead"><span>Path</span><span>Matches</span><span>Backend name</span><span>Servers (address:port)</span><span>Health check path</span><span /></div>
          <div v-for="(t, i) in f.routes" :key="i" class="hxw-route">
            <input v-model="t.path" class="input mono" :placeholder="t.ex.path || '/service'" aria-label="Path">
            <select v-model="t.kind" class="select" aria-label="Matches"><option v-for="[k, l] in MATCH" :key="k" :value="k">{{ l }}</option></select>
            <input v-model="t.backend" class="input mono" :placeholder="t.ex.name" aria-label="Backend name">
            <div class="hxw-servers">
              <div v-for="(r, j) in t.servers" :key="j" class="hxw-srv"><span class="hint mono hxw-sname">{{ srvName(t, j) }}</span>
                <input v-model="r.address" class="input mono" :placeholder="t.ex.servers[j]?.address || '172.2.2.4:32135'" :aria-label="`Server ${j + 1}`">
                <button class="btn icon-only ghost sm" type="button" aria-label="Remove server" :disabled="t.servers.length === 1" @click="t.servers.splice(j, 1)"><Icon name="x" /></button></div>
              <button class="link sm" type="button" @click="t.servers.push({ address: '', backup: false })">+ server</button></div>
            <input v-model="t.check" class="input mono" placeholder="(TCP check)" aria-label="Health check path">
            <button class="btn icon-only ghost" type="button" aria-label="Remove route" @click="f.routes.splice(i, 1)"><Icon name="x" /></button>
          </div>
          <button class="btn sm" type="button" style="align-self:flex-start"
            @click="f.routes.push(target({ name: `inbound-svc${f.routes.length + 1}`, path: `/svc${f.routes.length + 1}`, check: '', servers: [{ address: '172.2.2.1:32135' }, { address: '172.2.2.2:32135' }, { address: '172.2.2.3:32135' }] }))">Add route</button>
          <label class="pill" style="align-self:flex-start;margin-top:6px"><input v-model="f.everythingElse" type="checkbox"> Send everything else to a default backend</label>
          <div v-if="f.everythingElse && f.main" class="hxw-route">
            <span class="hint" style="align-self:center">everything else</span><span />
            <input v-model="f.main.backend" class="input mono" :placeholder="f.main.ex.name" aria-label="Default backend name">
            <div class="hxw-servers">
              <div v-for="(r, j) in f.main.servers" :key="j" class="hxw-srv"><span class="hint mono hxw-sname">{{ srvName(f.main, j) }}</span>
                <input v-model="r.address" class="input mono" :placeholder="f.main.ex.servers[j]?.address || '172.2.2.1:32080'" :aria-label="`Default server ${j + 1}`">
                <button class="btn icon-only ghost sm" type="button" aria-label="Remove server" :disabled="f.main.servers.length === 1" @click="f.main.servers.splice(j, 1)"><Icon name="x" /></button></div>
              <button class="link sm" type="button" @click="f.main.servers.push({ address: '', backup: false })">+ server</button></div>
            <input v-model="f.main.check" class="input mono" placeholder="(TCP check)" aria-label="Default health check path"><span />
          </div>
          <span class="hint"><b>/order and /order/…</b> is written as <code>{ path /order } || { path_beg /order/ }</code>: it matches /order and /order/123 but not /orders. Longer paths are checked first. Leave the health check path empty for a TCP check, or enter e.g. <code>/health</code> for an HTTP check.</span>
        </div></div>

      <!-- one backend -->
      <div v-else-if="f.main" class="form-row"><label class="lbl">Backend</label>
        <div class="hxw-row">
          <input v-model="f.main.backend" class="input mono" :placeholder="f.main.ex.name" aria-label="Backend name" style="max-width:260px">
          <input v-if="http" v-model="f.main.check" class="input mono" placeholder="health check path (empty = TCP check)" aria-label="Health check path" style="max-width:280px"></div>
        <div v-for="(r, j) in f.main.servers" :key="j" class="assign-row">
          <span class="hint mono" style="min-width:150px">{{ srvName(f.main, j) }}</span>
          <input v-model="r.address" class="input mono" :placeholder="f.main.ex.servers[j]?.address || (http ? '172.2.2.4:8080' : `172.3.3.3:${uc.port}`)" :aria-label="`Server ${j + 1}`">
          <label class="pill"><input v-model="r.backup" type="checkbox"> Backup</label>
          <button class="btn icon-only ghost" type="button" aria-label="Remove server" :disabled="f.main.servers.length === 1" @click="f.main.servers.splice(j, 1)"><Icon name="x" /></button></div>
        <button class="btn sm" type="button" style="align-self:flex-start" @click="f.main.servers.push({ address: '', backup: false })">Add server</button>
        <span class="hint">Empty fields use the example shown in grey. A backup server only gets connections when the others are down.</span></div>

      <div class="form-row"><label class="lbl">Backend settings</label>
        <HxBackendSettings v-model="f.settings" hide-mode :hide-check="http" /></div>

      <div v-if="err" class="test-result show err"><Icon name="crit" /><span>{{ err }}</span></div>
    </template>

    <template #actions><button class="btn ghost" type="button" @click="step === 'details' ? (step = 'pick') : emit('close')">{{ step === "details" ? "Back" : "Cancel" }}</button><span style="flex:1" />
      <button v-if="step === 'details'" class="btn primary" type="button" @click="submit">Review change</button></template>
  </Modal>
</template>

<style scoped>
.grow { flex: 1; }
.mono { font-family: var(--mono); }
.hxw-group h3 { margin: 6px 0 8px; font-size: 12px; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); font-weight: 600; }
.hxw-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 8px; }
.hxw-card { display: flex; gap: 10px; align-items: flex-start; padding: 12px; border: 1px solid var(--border); border-radius: 10px; background: none; color: var(--text); font: inherit; text-align: left; cursor: pointer; }
.hxw-card:hover { border-color: var(--accent); background: var(--surface-hover); }
.hxw-card .icon { width: 18px; height: 18px; color: var(--accent); flex: none; margin-top: 2px; }
.hxw-card > span { display: flex; flex-direction: column; gap: 3px; }
.hxw-card .hint { font-size: 12.5px; line-height: 1.4; }
.hxw-top { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.hxw-row { display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-end; }
.hxw-row .form-row { flex: 1; min-width: 140px; }
.hxw-box { display: flex; flex-direction: column; gap: 10px; padding: 12px; border: 1px solid var(--border); border-radius: 10px; }
.hxw-checks { display: flex; gap: 8px; flex-wrap: wrap; }
.hxw-routes { display: flex; flex-direction: column; gap: 8px; }
.hxw-rhead, .hxw-route { display: grid; grid-template-columns: 1fr 140px 1.1fr 1.9fr 0.9fr 34px; gap: 8px; align-items: start; }
.hxw-rhead { font-size: 11.5px; color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: .04em; }
.hxw-servers { display: flex; flex-direction: column; gap: 4px; align-items: flex-start; }
.hxw-srv { display: flex; gap: 4px; width: 100%; align-items: center; }
.hxw-sname { min-width: 64px; font-size: 11.5px; }
.link.sm { font-size: 12.5px; }
@media (max-width: 860px) { .hxw-rhead { display: none; } .hxw-route { grid-template-columns: 1fr; padding-bottom: 10px; border-bottom: 1px solid var(--border); } }
</style>
