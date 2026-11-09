<script setup lang="ts">
// Live access log (parsed: status, request, site, upstream server, time) and the error log.
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import Icon from "@/components/Icon.vue";
import { ng, siteName } from "@/lib/nginx";
import { useNginx } from "@/stores/nginx";

const n = useNginx();
const view = ref<"access" | "errors">("access");
const lines = ref<any[]>([]), errs = ref<any[]>([]), paths = ref<string[]>([]), formats = ref<Record<string, string>>({});
const f = reactive({ q: "", status: "all", paused: false });
let after = 0, timer = 0;
async function poll() {
  if (f.paused || n.currentId == null) return;
  try {
    const r = await ng.logs(n.currentId, after);
    paths.value = r.paths; formats.value = r.formats;
    if (r.lines.length) { after = r.lines[r.lines.length - 1].seq; lines.value = [...lines.value, ...r.lines].slice(-2000); }
  } catch { /* shown by the page banner */ }
}
async function loadErrors() { if (n.currentId != null) try { errs.value = (await ng.errors(n.currentId)).lines.reverse(); } catch { errs.value = []; } }
onMounted(() => { poll(); loadErrors(); timer = window.setInterval(() => { poll(); if (view.value === "errors") loadErrors(); }, 2000); });
onBeforeUnmount(() => clearInterval(timer));
const sites = computed(() => new Map((n.data?.config.sites || []).map((s) => [s.id, siteName(s)])));
const shown = computed(() => {
  const q = f.q.trim().toLowerCase();
  return lines.value.filter((l) => (f.status === "all" || String(l.status)[0] === f.status[0])
    && (!q || `${l.uri} ${l.host || ""} ${l.ip || ""} ${l.upstream_server || ""} ${l.status}`.toLowerCase().includes(q))).slice(-500).reverse();
});
const cls = (s: number) => (s >= 500 ? "s5" : s >= 400 ? "s4" : s >= 300 ? "s3" : "s2");
const noLog = computed(() => !paths.value.length);
</script>

<template>
  <div class="toolbar">
    <div class="segmented" role="group"><button type="button" :aria-pressed="view === 'access'" @click="view = 'access'">Requests</button>
      <button type="button" :aria-pressed="view === 'errors'" @click="view = 'errors'; loadErrors()">Error log</button></div>
    <template v-if="view === 'access'">
      <label class="search"><Icon name="search" /><input v-model="f.q" class="input" placeholder="Filter by path, host, client or server" aria-label="Filter requests"></label>
      <div class="segmented" role="group"><button v-for="v in ['all', '2xx', '3xx', '4xx', '5xx']" :key="v" type="button" :aria-pressed="f.status === v" @click="f.status = v">{{ v === "all" ? "All" : v }}</button></div>
      <button class="btn" type="button" @click="f.paused = !f.paused"><Icon :name="f.paused ? 'play' : 'pause'" /><span>{{ f.paused ? "Resume" : "Pause" }}</span></button>
    </template>
    <span class="grow" /><span v-if="view === 'access'" class="hint mono">{{ paths.join(", ") }}</span>
  </div>
  <template v-if="view === 'access'">
    <div v-if="noLog" class="banner show info"><Icon name="warn" /><span class="msg">No access log the agent can read. Log to a file (not only stdout), e.g. <code>access_log /var/log/nginx/k10y-access.log k10y;</code></span></div>
    <section class="card flush"><div class="card-body table-wrap">
      <table class="data ng-log"><thead><tr><th>Time</th><th>Status</th><th>Request</th><th>Site</th><th>Upstream server</th><th class="num">Time</th><th>Client</th></tr></thead>
        <tbody><tr v-for="l in shown" :key="l.seq">
          <td class="dim mono">{{ (l.time || "").replace("T", " ").slice(5, 19) }}</td>
          <td><span class="st" :class="cls(l.status)">{{ l.status }}</span></td>
          <td class="mono ng-req"><b>{{ l.method }}</b> {{ l.uri }}</td>
          <td>{{ sites.get(l.site) || l.host || "–" }}</td>
          <td class="mono dim">{{ l.upstream ? `${l.upstream} → ${l.upstream_server}` : l.upstream_addr || "–" }}</td>
          <td class="num mono">{{ l.rt != null ? `${Math.round(l.rt * 1000)} ms` : "–" }}</td>
          <td class="mono dim">{{ l.ip }}</td></tr>
          <tr v-if="!shown.length"><td colspan="7" class="dim">{{ lines.length ? "No request matches." : "Waiting for requests…" }}</td></tr></tbody></table>
    </div></section>
  </template>
  <section v-else class="card flush"><div class="card-body table-wrap">
    <table class="data"><thead><tr><th>Time</th><th>Level</th><th>Message</th></tr></thead>
      <tbody><tr v-for="(e, i) in errs" :key="i"><td class="dim mono">{{ e.time || "–" }}</td>
        <td><span class="st" :class="e.level === 'error' || e.level === 'crit' || e.level === 'emerg' ? 's5' : e.level === 'warn' ? 's4' : 's2'">{{ e.level || "?" }}</span></td>
        <td class="mono ng-req">{{ e.error ? `${e.path}: ${e.error}` : e.message }}</td></tr>
        <tr v-if="!errs.length"><td colspan="3" class="dim">The error log is empty or can't be read.</td></tr></tbody></table>
  </div></section>
</template>

<style scoped>
.ng-log td { font-size: 13px; }
.ng-req { white-space: normal; overflow-wrap: anywhere; min-width: 220px; }
.st { font-family: var(--mono); font-weight: 650; font-size: 12.5px; padding: 1px 6px; border-radius: 6px; }
.st.s2 { color: var(--good); } .st.s3 { color: var(--accent); } .st.s4 { color: var(--warning); } .st.s5 { color: var(--critical); }
</style>
