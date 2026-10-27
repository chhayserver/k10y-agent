<script setup lang="ts">
// New frontend, the guided way: choose what you are setting up (microservices by path, a web app, a database…),
// fill in only what that setup needs (examples are pre-filled as placeholders), and get one reviewed change that
// creates the frontend and every backend with its servers.
import { computed, reactive, ref, watch } from "vue";
import Icon from "@/components/Icon.vue";
import Modal from "@/components/Modal.vue";
import { hx, type HxBackend, type HxFrontend, type HxOp } from "@/lib/haproxy";
import { USE_CASES, backendChanges, backendData, splitHostPort, type Settings, type UseCase } from "@/lib/haproxyCatalog";
import HxBackendSettings from "./HxBackendSettings.vue";

const props = defineProps<{ open: boolean; instanceId: number; backends: HxBackend[]; frontends: HxFrontend[] }>();
const emit = defineEmits<{ close: []; review: [ops: HxOp[], title: string]; custom: [] }>();

interface Row { address: string; backup: boolean }
interface Target { path: string; backend: string; check: string; servers: Row[]; ex: UseCase["backends"][number] }
const step = ref<"pick" | "details">("pick"), uc = ref<UseCase>(USE_CASES[0]), err = ref("");
const certs = ref<any[]>([]);
const f = reactive({ name: "", port: 80 as number | string, tls: false, cert: "", redirect: true, everythingElse: true,
  routes: [] as Target[], main: null as Target | null, settings: backendData(USE_CASES[0]) as Settings });

const GROUPS: [string, string][] = [["web", "Web and APIs (HTTP)"], ["data", "Databases (TCP)"], ["other", "Other"]];
const target = (ex: UseCase["backends"][number]): Target => ({ path: "", backend: "", check: "", servers: ex.servers.map((x) => ({ address: "", backup: !!x.backup })), ex });

function pick(u: UseCase) {
  uc.value = u; err.value = "";
  Object.assign(f, { name: "", port: "", tls: false, cert: certs.value[0]?.name || "", redirect: true, everythingElse: true, settings: backendData(u) });
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
const usedPorts = computed(() => new Set(props.frontends.flatMap((x) => x.binds.map((b) => Number(b.port)))));
const ports = computed(() => (http.value && f.tls ? [443, ...(f.redirect ? [80] : [])] : [Number(f.port) || uc.value.port]));
const clash = computed(() => ports.value.filter((p) => usedPorts.value.has(p)));
const allTargets = computed(() => [...f.routes, ...(f.main && (uc.value.shape === "proxy" || f.everythingElse) ? [f.main] : [])]);
const nameOf = (t: Target) => (t.backend.trim() || t.ex.name);

function submit() {
  err.value = "";
  const fname = (f.name.trim() || uc.value.frontend);
  const existingB = new Set(props.backends.map((b) => b.name)), existingF = new Set(props.frontends.map((x) => x.name));
  if (existingF.has(fname)) { err.value = `A frontend named ${fname} already exists. Choose another name.`; return; }
  if (clash.value.length) { err.value = `Port ${clash.value.join(", ")} is already used by another frontend.`; return; }
  if (http.value && f.tls && !f.cert) { err.value = "Choose a certificate for HTTPS, or upload one on the Certificates page first."; return; }
  const ops: HxOp[] = [], names = new Set<string>();
  for (const t of allTargets.value) {
    const n = nameOf(t);
    if (existingB.has(n) || names.has(n)) { err.value = `Backend name ${n} is already used. Give each backend its own name.`; return; }
    if (t.path !== undefined && f.routes.includes(t) && !(t.path.trim() || t.ex.path || "").startsWith("/")) { err.value = `The path for ${n} must start with /, e.g. /api/${n}`; return; }
    names.add(n);
    const s: Settings = http.value ? { ...f.settings, check: { type: "http", method: "GET", uri: (t.check.trim() || t.ex.check || "/health"), host: "" } } : f.settings;
    ops.push({ op: "create", kind: "backend", name: n, data: backendChanges(s, null) });
    const rows = t.servers.map((r, i) => ({ ...r, address: r.address.trim() || t.ex.servers[i]?.address || "" })).filter((r) => r.address);
    if (!rows.length) { err.value = `Add at least one server to ${n}.`; return; }
    for (const [i, r] of rows.entries()) {
      const hp = splitHostPort(r.address);
      if (!hp) { err.value = `${n}, server ${i + 1}: write it as address:port, e.g. 10.0.1.11:8080`; return; }
      ops.push({ op: "create", kind: "server", backend: n, name: `${n}-${i + 1}`, data: { ...hp, check: true, backup: r.backup } });
    }
  }
  const routes = f.routes.map((t) => ({ kind: "path_prefix", value: (t.path.trim() || t.ex.path || "").replace(/\/+$/, "") || "/", backend: nameOf(t) }))
    .sort((a, b) => b.value.length - a.value.length);     // longer paths first, so /api/users wins over /api
  const binds = http.value && f.tls ? [{ address: "*", port: 443, ssl: true, certificate: f.cert }, ...(f.redirect ? [{ address: "*", port: 80 }] : [])] : [{ address: "*", port: ports.value[0] }];
  const fe: Record<string, any> = { mode: uc.value.mode, binds, default_backend: f.main && (uc.value.shape === "proxy" || f.everythingElse) ? nameOf(f.main) : null,
    routes: uc.value.shape === "paths" ? routes : [], timeouts: { client: uc.value.timeouts?.client || 0 } };
  if (http.value && f.tls && f.redirect) fe.policies = { allow_ips: [], redirect_https: true, rate_limit: null };
  ops.push({ op: "create", kind: "frontend", name: fname, data: fe });
  emit("review", ops, `New ${uc.value.label.toLowerCase()}: frontend ${fname} with ${names.size} backend${names.size > 1 ? "s" : ""}`);
}
</script>

<template>
  <Modal :open="open" :title="step === 'pick' ? 'New frontend' : uc.label" wide @close="emit('close')">
    <!-- step 1: what are you setting up? -->
    <template v-if="step === 'pick'">
      <p class="hint" style="margin:0">What are you setting up? k10y fills in the right protocol, balancing, health check and timeouts, with example values you can keep or change.</p>
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
        <div v-if="!(http && f.tls)" class="form-row"><label class="lbl" for="hxw-port">Listen on port</label>
          <input id="hxw-port" v-model="f.port" class="input" type="number" min="1" max="65535" :placeholder="String(uc.port)" style="max-width:140px"></div>
      </div>
      <div v-if="http" class="hxw-tls">
        <label class="pill"><input v-model="f.tls" type="checkbox"> Serve HTTPS (TLS on port 443)</label>
        <template v-if="f.tls">
          <select v-model="f.cert" class="select" style="max-width:240px" aria-label="Certificate"><option value="" disabled>Certificate…</option>
            <option v-for="c in certs" :key="c.name" :value="c.name">{{ c.name }}</option></select>
          <label class="pill"><input v-model="f.redirect" type="checkbox"> Also listen on 80 and redirect to HTTPS</label>
          <span v-if="!certs.length" class="hint" style="color:var(--critical)">No certificates yet: upload one on the Certificates page.</span></template>
      </div>
      <span v-if="clash.length" class="hint" style="color:var(--critical)">Port {{ clash.join(", ") }} is already used by another frontend on this HAProxy.</span>

      <!-- microservices: one row per path -->
      <div v-if="uc.shape === 'paths'" class="form-row"><label class="lbl">Routes</label>
        <div class="hxw-routes">
          <div class="hxw-rhead"><span>Path starts with</span><span>Backend name</span><span>Servers (address:port)</span><span>Health check path</span><span /></div>
          <div v-for="(t, i) in f.routes" :key="i" class="hxw-route">
            <input v-model="t.path" class="input mono" :placeholder="t.ex.path || '/api/service'" aria-label="Path">
            <input v-model="t.backend" class="input mono" :placeholder="t.ex.name" aria-label="Backend name">
            <div class="hxw-servers">
              <div v-for="(r, j) in t.servers" :key="j" class="hxw-srv"><input v-model="r.address" class="input mono" :placeholder="t.ex.servers[j]?.address || '10.0.1.40:8080'" :aria-label="`Server ${j + 1}`">
                <button class="btn icon-only ghost sm" type="button" aria-label="Remove server" :disabled="t.servers.length === 1" @click="t.servers.splice(j, 1)"><Icon name="x" /></button></div>
              <button class="link sm" type="button" @click="t.servers.push({ address: '', backup: false })">+ server</button></div>
            <input v-model="t.check" class="input mono" :placeholder="t.ex.check || '/health'" aria-label="Health check path">
            <button class="btn icon-only ghost" type="button" aria-label="Remove route" @click="f.routes.splice(i, 1)"><Icon name="x" /></button>
          </div>
          <button class="btn sm" type="button" style="align-self:flex-start" @click="f.routes.push(target({ name: `svc${f.routes.length + 1}`, path: `/api/svc${f.routes.length + 1}`, check: '/health', servers: [{ address: '10.0.1.40:8080' }] }))">Add route</button>
          <label class="pill" style="align-self:flex-start;margin-top:6px"><input v-model="f.everythingElse" type="checkbox"> Send everything else to a default backend</label>
          <div v-if="f.everythingElse && f.main" class="hxw-route">
            <span class="hint" style="align-self:center">everything else</span>
            <input v-model="f.main.backend" class="input mono" :placeholder="f.main.ex.name" aria-label="Default backend name">
            <div class="hxw-servers">
              <div v-for="(r, j) in f.main.servers" :key="j" class="hxw-srv"><input v-model="r.address" class="input mono" :placeholder="f.main.ex.servers[j]?.address || '10.0.1.31:3000'" :aria-label="`Default server ${j + 1}`">
                <button class="btn icon-only ghost sm" type="button" aria-label="Remove server" :disabled="f.main.servers.length === 1" @click="f.main.servers.splice(j, 1)"><Icon name="x" /></button></div>
              <button class="link sm" type="button" @click="f.main.servers.push({ address: '', backup: false })">+ server</button></div>
            <input v-model="f.main.check" class="input mono" :placeholder="f.main.ex.check || '/'" aria-label="Default health check path"><span />
          </div>
          <span class="hint">Longer paths are checked first, so <code>/api/users</code> wins over <code>/api</code>. Without a default backend, other paths get a 503.</span>
        </div></div>

      <!-- one backend -->
      <div v-else-if="f.main" class="form-row"><label class="lbl">Backend</label>
        <div class="hxw-row">
          <input v-model="f.main.backend" class="input mono" :placeholder="f.main.ex.name" aria-label="Backend name" style="max-width:220px">
          <input v-if="http" v-model="f.main.check" class="input mono" :placeholder="`health check ${f.main.ex.check || '/health'}`" aria-label="Health check path" style="max-width:220px"></div>
        <div v-for="(r, j) in f.main.servers" :key="j" class="assign-row">
          <span class="hint mono" style="min-width:90px">{{ nameOf(f.main) }}-{{ j + 1 }}</span>
          <input v-model="r.address" class="input mono" :placeholder="f.main.ex.servers[j]?.address || (http ? '10.0.1.12:8080' : `10.0.3.12:${uc.port}`)" :aria-label="`Server ${j + 1}`">
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
.hxw-row .form-row { flex: 1; min-width: 180px; }
.hxw-tls { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.hxw-routes { display: flex; flex-direction: column; gap: 8px; }
.hxw-rhead, .hxw-route { display: grid; grid-template-columns: 1.1fr 1fr 1.5fr 1fr 34px; gap: 8px; align-items: start; }
.hxw-rhead { font-size: 11.5px; color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: .04em; }
.hxw-servers { display: flex; flex-direction: column; gap: 4px; align-items: flex-start; }
.hxw-srv { display: flex; gap: 4px; width: 100%; }
.link.sm { font-size: 12.5px; }
@media (max-width: 760px) { .hxw-rhead { display: none; } .hxw-route { grid-template-columns: 1fr; padding-bottom: 10px; border-bottom: 1px solid var(--border); } }
</style>
