<script setup lang="ts">
// A route (location): what it matches and what it does. Emits the form's data; the change dialog does the rest.
import { computed, reactive, watch } from "vue";
import Icon from "@/components/Icon.vue";
import Modal from "@/components/Modal.vue";
import type { NgLocation, NgUpstream } from "@/lib/nginx";

const props = defineProps<{ open: boolean; route?: NgLocation | null; upstreams: NgUpstream[]; siteName: string }>();
const emit = defineEmits<{ close: []; submit: [data: any] }>();
const KINDS = [
  { id: "proxy", label: "Proxy", hint: "Forward to an upstream or an address (an app, an API, a microservice)." },
  { id: "redirect", label: "Redirect", hint: "Send people to another URL (301 permanent, 302/307 temporary)." },
  { id: "static", label: "Static files", hint: "Serve files from a folder on this server." },
  { id: "return", label: "Fixed answer", hint: "Answer with a status and short text, e.g. a health check." },
];
const MATCHES = [["prefix", "Starts with"], ["exact", "Is exactly"], ["prefix-priority", "Starts with (before regex)"], ["regex", "Regex"], ["regex-i", "Regex, any case"]];
const f = reactive({ match: "prefix", path: "/", kind: "proxy", target: "", code: "301", text: "", websocket: false, timeout: "", alias: false, allow: "" });
watch(() => props.open, (o) => {
  if (!o) return;
  const r = props.route;
  if (!r) { Object.assign(f, { match: "prefix", path: "/api/", kind: "proxy", target: props.upstreams[0]?.name || "", code: "301", text: "", websocket: false, timeout: "", alias: false, allow: "" }); return; }
  const ret = (r.return || "").split(" ");
  Object.assign(f, { match: r.match === "named" ? "prefix" : r.match, path: r.path, kind: ["proxy", "redirect", "static", "return"].includes(r.kind) ? r.kind : "proxy",
    target: r.kind === "proxy" ? (r.upstream_known ? r.upstream : (r.proxy_pass || "").replace(/^https?:\/\//, "")) : r.kind === "redirect" ? ret.slice(1).join(" ") : r.alias || r.root || "",
    code: ret[0] || (r.kind === "redirect" ? "301" : "200"), text: r.kind === "return" ? ret.slice(1).join(" ").replace(/^"|"$/g, "") : "",
    websocket: r.websocket, timeout: r.timeout || "", alias: !!r.alias, allow: r.allow.join(", ") });
}, { immediate: true });
const lost = computed(() => props.route?.other?.filter((x) => x !== "location") || []);
const valid = computed(() => f.path.trim() && (f.kind === "return" || f.target.trim()));
function submit() {
  emit("submit", { match: f.match, path: f.path.trim(), kind: f.kind, target: f.target.trim(), code: f.code, text: f.text, websocket: f.websocket,
    timeout: f.timeout.trim() || undefined, alias: f.alias, allow: f.allow.split(/[\s,]+/).filter(Boolean) });
}
</script>

<template>
  <Modal :open="open" :title="route ? `Edit route ${route.path}` : `Add a route to ${siteName}`" wide @close="emit('close')">
    <div class="form-row"><span class="lbl">When the path</span><div class="field">
      <select v-model="f.match" class="select" aria-label="Match" style="max-width:240px"><option v-for="[v, l] in MATCHES" :key="v" :value="v">{{ l }}</option></select>
      <input v-model="f.path" class="input mono" placeholder="/api/" aria-label="Path"></div></div>
    <div class="form-row"><span class="lbl">Do</span><div class="segmented" role="group">
      <button v-for="k in KINDS" :key="k.id" type="button" :aria-pressed="f.kind === k.id" @click="f.kind = k.id">{{ k.label }}</button></div>
      <span class="hint">{{ KINDS.find((k) => k.id === f.kind)?.hint }}</span></div>
    <template v-if="f.kind === 'proxy'">
      <div class="form-row"><label class="lbl" for="rt-t">To</label>
        <input id="rt-t" v-model="f.target" class="input mono" list="rt-ups" placeholder="an upstream name, or 10.0.0.5:8080">
        <datalist id="rt-ups"><option v-for="u in upstreams" :key="u.id" :value="u.name">{{ u.servers.length }} servers</option></datalist>
        <span class="hint">An upstream spreads requests over several servers (add one on the Upstreams page). Add <code>/</code> at the end of an address to strip the route's path.</span></div>
      <div style="display:flex;gap:12px;flex-wrap:wrap;align-items:center">
        <label class="pill"><input v-model="f.websocket" type="checkbox"> WebSocket</label>
        <label class="pill" style="gap:8px">Timeout <input v-model="f.timeout" class="input sm" style="width:90px" placeholder="60s"></label></div>
    </template>
    <template v-else-if="f.kind === 'redirect'">
      <div class="form-row"><span class="lbl">Redirect</span><div class="field">
        <select v-model="f.code" class="select" aria-label="Code" style="max-width:220px"><option value="301">301 permanent</option><option value="308">308 permanent (keep method)</option>
          <option value="302">302 temporary</option><option value="307">307 temporary (keep method)</option></select>
        <input v-model="f.target" class="input mono" placeholder="https://new.example.com$request_uri" aria-label="To"></div></div>
    </template>
    <template v-else-if="f.kind === 'static'">
      <div class="form-row"><label class="lbl" for="rt-root">Folder</label><input id="rt-root" v-model="f.target" class="input mono" placeholder="/var/www/shop">
        <label class="pill" style="align-self:flex-start;margin-top:6px"><input v-model="f.alias" type="checkbox"> The folder replaces the route's path (<code>alias</code>, e.g. /static/ → /var/www/assets/)</label></div>
    </template>
    <template v-else>
      <div class="form-row"><span class="lbl">Answer</span><div class="field">
        <input v-model="f.code" class="input mono" style="max-width:100px" placeholder="200" aria-label="Status">
        <input v-model="f.text" class="input" placeholder="ok" aria-label="Text"></div></div>
    </template>
    <div class="form-row"><label class="lbl" for="rt-allow">Only from these IPs (optional)</label><input id="rt-allow" v-model="f.allow" class="input mono" placeholder="10.0.0.0/8, 192.168.1.20">
      <span class="hint">Everyone else gets 403.</span></div>
    <div v-if="lost.length" class="test-result show err"><Icon name="warn" /><span>This route also has <code>{{ lost.join(", ") }}</code>, which this form doesn't manage: saving removes them. To keep them, edit the file in Raw config instead.</span></div>
    <template #actions><button class="btn ghost" type="button" @click="emit('close')">Cancel</button><span style="flex:1" />
      <button class="btn primary" type="button" :disabled="!valid" @click="submit">Review</button></template>
  </Modal>
</template>
