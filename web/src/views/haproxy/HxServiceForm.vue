<script setup lang="ts">
// Create or edit a service (an HAProxy frontend): where it listens, where requests go, and access policies.
import { reactive, ref, watch } from "vue";
import Icon from "@/components/Icon.vue";
import Modal from "@/components/Modal.vue";
import { hx, ROUTE_KIND, type HxBackend, type HxFrontend, type HxOp } from "@/lib/haproxy";

const props = defineProps<{ open: boolean; frontend: HxFrontend | null; backends: HxBackend[]; instanceId: number }>();
const emit = defineEmits<{ close: []; review: [ops: HxOp[], title: string] }>();
type Bind = { address: string; port: number | string; ssl: boolean; certificate: string };
type Route = { kind: string; value: string; backend: string; header?: string };
const f = reactive({ name: "", mode: "http", binds: [] as Bind[], default_backend: "", routes: [] as Route[],
  allow: "", redirect_https: false, limit_on: false, limit_n: 100 as number | string, limit_period: 10 as number | string, err: "" });
const certs = ref<any[]>([]);

watch(() => props.open, async (o) => {
  if (!o) return;
  const fe = props.frontend, pol = fe?.policies;
  Object.assign(f, {
    name: fe?.name || "", mode: fe?.mode || "http", default_backend: fe?.default_backend || props.backends[0]?.name || "", err: "",
    binds: fe ? fe.binds.map((b) => ({ address: b.address, port: b.port, ssl: b.ssl, certificate: b.certificate || "" })) : [{ address: "*", port: 80, ssl: false, certificate: "" }],
    routes: fe && fe.simple_routes ? fe.routes.map((r) => ({ ...r })) : [],
    allow: (pol?.allow_ips || []).join("\n"), redirect_https: !!pol?.redirect_https,
    limit_on: !!pol?.rate_limit, limit_n: pol?.rate_limit?.requests || 100, limit_period: pol?.rate_limit?.period || 10,
  });
  try { certs.value = await hx.certificates(props.instanceId); } catch { certs.value = []; }
});
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const routeKey = (r: Route) => ({ kind: r.kind, value: r.value.trim(), backend: r.backend, ...(r.kind === "header" ? { header: (r.header || "").trim() } : {}) });

function submit() {
  f.err = "";
  if (!f.name.trim()) { f.err = "Give the service a name."; return; }
  if (!f.binds.length) { f.err = "Add at least one listen address."; return; }
  if (f.binds.some((b) => b.ssl && !b.certificate)) { f.err = "Choose a certificate for each TLS listen address (upload one on the Certificates page)."; return; }
  // Only send what changed, so the config diff shows exactly the edit and nothing else.
  const fe = props.frontend, data: Record<string, any> = {};
  const binds = f.binds.map((b) => ({ address: (b.address || "*").trim(), port: Number(b.port), ...(b.ssl ? { ssl: true, certificate: b.certificate } : {}) }));
  const oldBinds = fe?.binds.map((b) => ({ address: b.address, port: b.port, ...(b.ssl ? { ssl: true, certificate: b.certificate } : {}) }));
  if (!fe || f.mode !== fe.mode) data.mode = f.mode;
  if (!fe || !same(binds, oldBinds)) data.binds = binds;
  if (!fe || (f.default_backend || null) !== fe.default_backend) data.default_backend = f.default_backend || null;
  const routes = f.mode === "http" ? f.routes.map(routeKey) : [];
  if ((!fe || fe.simple_routes) && (!fe || !same(routes, fe.routes.map(routeKey)))) data.routes = routes;
  if (f.mode === "http") {
    const policies = { allow_ips: f.allow.split(/[\s,]+/).filter(Boolean), redirect_https: f.redirect_https,
      rate_limit: f.limit_on ? { requests: Number(f.limit_n), period: Number(f.limit_period) } : null };
    const old = fe?.policies;
    if (!fe || !same(policies, { allow_ips: old?.allow_ips || [], redirect_https: !!old?.redirect_https, rate_limit: old?.rate_limit || null }))
      if (fe || policies.allow_ips.length || policies.redirect_https || policies.rate_limit) data.policies = policies;
  }
  if (fe && !Object.keys(data).length) { f.err = "Nothing changed."; return; }
  emit("review", [{ op: fe ? "update" : "create", kind: "frontend", name: f.name.trim(), data }], fe ? `Edit service ${f.name}` : `New service ${f.name}`);
}
</script>
<template>
  <Modal :open="open" :title="frontend ? `Edit service ${frontend.name}` : 'New service'" wide @close="emit('close')">
    <p class="hint" style="margin:0">A service is an HAProxy <b>frontend</b>: the address and port clients connect to, the rules that pick a backend, and who may use it.</p>
    <div class="two-col">
      <div class="form-row"><label class="lbl">Name</label><input v-model="f.name" class="input" :disabled="!!frontend" placeholder="e.g. web" style="font-family:var(--mono)"></div>
      <div class="form-row"><label class="lbl">Protocol</label><div class="segmented" role="group">
        <button type="button" :aria-pressed="f.mode === 'http'" @click="f.mode = 'http'">HTTP</button><button type="button" :aria-pressed="f.mode === 'tcp'" @click="f.mode = 'tcp'">TCP</button></div></div>
    </div>

    <div class="form-row"><label class="lbl">Listen on</label>
      <div v-for="(b, i) in f.binds" :key="i" class="assign-row">
        <input v-model="b.address" class="input" placeholder="* (all addresses)" style="font-family:var(--mono);max-width:180px" aria-label="Address">
        <span class="hint">:</span><input v-model="b.port" class="input" type="number" min="1" max="65535" style="max-width:100px" aria-label="Port">
        <label class="pill"><input v-model="b.ssl" type="checkbox"> TLS</label>
        <select v-if="b.ssl" v-model="b.certificate" class="select" style="max-width:220px" aria-label="Certificate">
          <option value="" disabled>Certificate…</option><option v-for="c in certs" :key="c.name" :value="c.name">{{ c.name }}</option></select>
        <button class="btn icon-only ghost" type="button" aria-label="Remove listen address" :disabled="f.binds.length === 1" @click="f.binds.splice(i, 1)"><Icon name="x" /></button></div>
      <button class="btn sm" type="button" style="align-self:flex-start" @click="f.binds.push({ address: '*', port: '', ssl: false, certificate: '' })">Add address</button>
      <span v-if="f.binds.some((b) => b.ssl) && !certs.length" class="hint" style="color:var(--critical)">No certificates uploaded yet: add one on the Certificates page first.</span></div>

    <div class="form-row"><label class="lbl">Default backend</label>
      <select v-model="f.default_backend" class="select"><option value="">None</option><option v-for="b in backends" :key="b.name" :value="b.name">{{ b.name }}</option></select>
      <span class="hint">Requests that match no route go here.</span></div>

    <template v-if="f.mode === 'http'">
      <div class="form-row"><label class="lbl">Routes</label>
        <template v-if="!frontend || frontend.simple_routes">
          <div v-for="(r, i) in f.routes" :key="i" class="assign-row">
            <select v-model="r.kind" class="select" style="max-width:160px" aria-label="Match"><option v-for="k in ['path_prefix', 'path', 'host', 'header', 'src']" :key="k" :value="k">{{ ROUTE_KIND[k] }}</option></select>
            <input v-if="r.kind === 'header'" v-model="r.header" class="input" placeholder="X-Canary" style="max-width:130px;font-family:var(--mono)" aria-label="Header name">
            <input v-model="r.value" class="input" :placeholder="{ host: 'api.example.com', header: 'yes', src: '10.0.0.0/8,192.168.1.0/24' }[r.kind] || '/api'" style="font-family:var(--mono)" aria-label="Value">
            <span class="hint">→</span>
            <select v-model="r.backend" class="select" style="max-width:170px" aria-label="Backend"><option v-for="b in backends" :key="b.name" :value="b.name">{{ b.name }}</option></select>
            <button class="btn icon-only ghost" type="button" aria-label="Remove route" @click="f.routes.splice(i, 1)"><Icon name="x" /></button></div>
          <button class="btn sm" type="button" style="align-self:flex-start" @click="f.routes.push({ kind: 'path_prefix', value: '', backend: backends[0]?.name || '' })">Add route</button>
          <span class="hint">Checked from top to bottom; the first match wins. "Header" is handy for canaries (e.g. <code>X-Canary: yes</code> → new version).</span>
        </template>
        <div v-else class="test-result show"><Icon name="warn" /><span>This service has custom routing rules this form can't edit safely; they're kept as they are. Edit them in <b>Raw config</b>.</span></div>
      </div>

      <div class="form-row"><label class="lbl">Access</label>
        <label class="pill" style="align-self:flex-start"><input v-model="f.redirect_https" type="checkbox"> Redirect plain HTTP to HTTPS (301)</label>
        <textarea v-model="f.allow" class="input" rows="2" style="font-family:var(--mono);height:auto;padding:8px" placeholder="Allow only these client IPs or networks (empty = everyone), e.g. 10.0.0.0/8 192.168.1.0/24" />
        <div class="assign-row"><label class="pill"><input v-model="f.limit_on" type="checkbox"> Rate limit per client IP</label>
          <template v-if="f.limit_on"><input v-model="f.limit_n" class="input" type="number" min="1" style="max-width:110px" aria-label="Requests">
            <span class="hint">requests per</span><input v-model="f.limit_period" class="input" type="number" min="1" max="3600" style="max-width:90px" aria-label="Seconds"><span class="hint">seconds, then 429</span></template></div>
        <span v-if="frontend?.policies.custom_rules" class="hint">This service also has {{ frontend.policies.custom_rules }} custom request rule(s); they're kept after these.</span></div>
    </template>
    <div v-if="f.err" class="test-result show err"><Icon name="crit" /><span>{{ f.err }}</span></div>
    <template #actions><button class="btn ghost" type="button" @click="emit('close')">Cancel</button><span style="flex:1" />
      <button class="btn primary" type="button" @click="submit">Review change</button></template>
  </Modal>
</template>
