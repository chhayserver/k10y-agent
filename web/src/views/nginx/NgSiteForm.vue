<script setup lang="ts">
// A new site from what it's for (an app, a static website, a redirect), or new names and ports for an existing one.
import { computed, reactive, watch } from "vue";
import Modal from "@/components/Modal.vue";
import type { NgSite, NgUpstream } from "@/lib/nginx";

const props = defineProps<{ open: boolean; site?: NgSite | null; upstreams: NgUpstream[] }>();
const emit = defineEmits<{ close: []; submit: [data: any] }>();
const USES = [
  { id: "app", label: "App or API", hint: "Forward everything to your app's servers (spread over several with an upstream)." },
  { id: "static", label: "Static website", hint: "Serve files from a folder (a built front-end, docs, a landing page)." },
  { id: "redirect", label: "Redirect a domain", hint: "Send an old or extra domain to another address, keeping the path." },
];
const f = reactive({ use: "app", names: "", tls: false, cert: "", key: "", redirect_http: true, upstream: "new", up_name: "", servers: "", target: "", root: "", to: "", listen: "" });
watch(() => props.open, (o) => {
  if (!o) return;
  if (props.site) { Object.assign(f, { names: props.site.server_names.filter((x) => x !== "_").join(" "), listen: props.site.listen.map((l) => l.text).join("\n") }); return; }
  Object.assign(f, { use: "app", names: "", tls: false, cert: "/etc/nginx/certs/example.com.pem", key: "/etc/nginx/certs/example.com.key", redirect_http: true,
    upstream: props.upstreams.length ? props.upstreams[0].name : "new", up_name: "", servers: "10.0.0.11:8080\n10.0.0.12:8080", root: "/var/www/example", to: "https://www.example.com", listen: "" });
}, { immediate: true });
const names = computed(() => f.names.split(/[\s,]+/).filter(Boolean));
const upName = computed(() => f.up_name || (names.value[0] || "app").replace(/[^A-Za-z0-9]+/g, "_") + "_app");
const valid = computed(() => {
  if (!names.value.length) return false;
  if (props.site) return true;
  if (f.tls && (!f.cert || !f.key)) return false;
  return f.use === "app" ? (f.upstream !== "new" || f.servers.trim()) : f.use === "static" ? f.root.startsWith("/") : !!f.to;
});
function submit() {
  if (props.site) { emit("submit", { server_names: names.value, listen: f.listen.split("\n").map((x) => x.trim()).filter(Boolean) }); return; }
  const loc = f.use === "app" ? { path: "/", kind: "proxy", target: f.upstream === "new" ? upName.value : f.upstream }
    : f.use === "static" ? { path: "/", kind: "static", target: f.root } : { path: "/", kind: "redirect", code: "301", target: `${f.to.replace(/\/$/, "")}$request_uri` };
  emit("submit", { server_names: names.value, tls: f.tls, ssl_certificate: f.tls ? f.cert : undefined, ssl_certificate_key: f.tls ? f.key : undefined,
    redirect_http: f.tls && f.redirect_http, locations: [loc],
    upstream: f.use === "app" && f.upstream === "new" ? { name: upName.value, servers: f.servers.split(/\s+/).filter(Boolean).map((address) => ({ address })) } : undefined });
}
</script>

<template>
  <Modal :open="open" :title="site ? `Names and ports of ${site.server_names.join(', ')}` : 'Add a site'" wide @close="emit('close')">
    <template v-if="!site">
      <div class="form-row"><span class="lbl">What is it?</span><div class="segmented" role="group">
        <button v-for="u in USES" :key="u.id" type="button" :aria-pressed="f.use === u.id" @click="f.use = u.id">{{ u.label }}</button></div>
        <span class="hint">{{ USES.find((u) => u.id === f.use)?.hint }}</span></div>
    </template>
    <div class="form-row"><label class="lbl" for="st-n">Domain names</label><input id="st-n" v-model="f.names" class="input mono" placeholder="shop.example.com www.shop.example.com">
      <span class="hint">Separate with spaces. <code>*.example.com</code> matches every subdomain.</span></div>
    <template v-if="site">
      <div class="form-row"><label class="lbl" for="st-l">Listens on (one per line)</label><textarea id="st-l" v-model="f.listen" class="input mono" rows="3" placeholder="443 ssl" />
        <span class="hint">For example <code>80</code>, <code>443 ssl</code>, <code>[::]:443 ssl</code>, <code>8080 default_server</code>. Only these lines and the names change; routes stay as they are.</span></div>
    </template>
    <template v-else>
      <template v-if="f.use === 'app'">
        <div class="form-row"><label class="lbl" for="st-up">Send to</label>
          <select id="st-up" v-model="f.upstream" class="select" style="max-width:320px"><option value="new">New upstream…</option>
            <option v-for="u in upstreams" :key="u.id" :value="u.name">upstream {{ u.name }} ({{ u.servers.length }} servers)</option></select></div>
        <template v-if="f.upstream === 'new'">
          <div class="form-row"><label class="lbl" for="st-un">Upstream name</label><input id="st-un" v-model="f.up_name" class="input mono" :placeholder="upName"></div>
          <div class="form-row"><label class="lbl" for="st-s">Servers (one per line)</label><textarea id="st-s" v-model="f.servers" class="input mono" rows="3" />
            <span class="hint">Round robin; change weights, backups and balancing on the Upstreams page later.</span></div>
        </template>
      </template>
      <div v-else-if="f.use === 'static'" class="form-row"><label class="lbl" for="st-r">Folder</label><input id="st-r" v-model="f.root" class="input mono" placeholder="/var/www/example"></div>
      <div v-else class="form-row"><label class="lbl" for="st-to">Send people to</label><input id="st-to" v-model="f.to" class="input mono" placeholder="https://www.example.com">
        <span class="hint">301 permanent; the rest of the path is kept.</span></div>
      <label class="pill" style="align-self:flex-start"><input v-model="f.tls" type="checkbox"> HTTPS (TLS, port 443)</label>
      <template v-if="f.tls">
        <div class="form-row"><label class="lbl" for="st-c">Certificate file</label><input id="st-c" v-model="f.cert" class="input mono"></div>
        <div class="form-row"><label class="lbl" for="st-k">Key file</label><input id="st-k" v-model="f.key" class="input mono">
          <span class="hint">Files on the Nginx server (e.g. from certbot: <code>/etc/letsencrypt/live/&lt;domain&gt;/fullchain.pem</code> and <code>privkey.pem</code>).</span></div>
        <label class="pill" style="align-self:flex-start"><input v-model="f.redirect_http" type="checkbox"> Also redirect http:// to https://</label>
      </template>
    </template>
    <template #actions><button class="btn ghost" type="button" @click="emit('close')">Cancel</button><span style="flex:1" />
      <button class="btn primary" type="button" :disabled="!valid" @click="submit">Review</button></template>
  </Modal>
</template>
