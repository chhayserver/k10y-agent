<script setup lang="ts">
// Every server block as a card: names, ports, TLS, and its routes (locations) with where each goes and live numbers.
import { computed, nextTick, onMounted, reactive } from "vue";
import Badge from "@/components/Badge.vue";
import Icon from "@/components/Icon.vue";
import { KIND, listenText, locationText, rate, siteName, upstreamHealth, type NgLocation, type NgSite } from "@/lib/nginx";
import { useNginx } from "@/stores/nginx";

const props = defineProps<{ focus?: string }>();
const n = useNginx();
const f = reactive({ q: "", type: "all" });
const d = computed(() => n.data!);
const last = computed(() => n.last);
function flat(locs: NgLocation[], depth = 0): (NgLocation & { depth: number })[] {
  return locs.flatMap((l) => [{ ...l, depth }, ...flat(l.locations || [], depth + 1)]);
}
const sites = computed(() => d.value.config.sites.filter((s) => {
  const q = f.q.trim().toLowerCase();
  const text = `${s.server_names.join(" ")} ${listenText(s)} ${flat(s.locations).map((l) => `${l.path} ${l.proxy_pass || ""} ${l.return || ""}`).join(" ")}`.toLowerCase();
  const typeOk = f.type === "all" || (f.type === "tls" ? s.ssl : f.type === "errors" ? (last.value?.sites?.[s.id]?.e5_pct ?? 0) >= 1 : s.kind === f.type || (f.type === "proxy" && s.kind === "mixed"));
  return typeOk && (!q || text.includes(q));
}));
const target = (l: NgLocation) => l.upstream && l.upstream_known ? `upstream ${l.upstream}` : l.proxy_pass ? l.proxy_pass
  : l.return ? `return ${l.return}` : l.alias ? `files ${l.alias}` : l.root ? `files ${l.root}` : l.try_files ? `try ${l.try_files}` : "–";
const upstream = (name: string | null) => d.value.config.upstreams.find((u) => u.name === name);
const short = (file: string) => file.split("/").slice(-2).join("/");
const nums = (key: string, kind: "sites" | "locations") => last.value?.[kind]?.[key];
onMounted(async () => {
  if (props.focus) { await nextTick(); document.getElementById(`site-${props.focus}`)?.scrollIntoView({ block: "center" }); }
});
</script>

<template>
  <div class="toolbar">
    <label class="search"><Icon name="search" /><input v-model="f.q" class="input" placeholder="Search names, ports, paths or targets" aria-label="Search sites"></label>
    <div class="segmented" role="group" aria-label="Filter">
      <button v-for="[v, l] in [['all', 'All'], ['proxy', 'Proxy'], ['static', 'Static'], ['redirect', 'Redirect'], ['tcp', 'TCP'], ['tls', 'TLS'], ['errors', 'Errors']]" :key="v"
        type="button" :aria-pressed="f.type === v" @click="f.type = v">{{ l }}</button></div>
    <span class="result-count">{{ sites.length }} of {{ d.config.sites.length }}</span>
  </div>
  <p v-if="!sites.length" class="hint">No site matches.</p>
  <section v-for="s in sites" :id="`site-${s.id}`" :key="s.id" class="card ng-site" :class="{ focus: focus === s.id }">
    <div class="card-head ng-site-head">
      <div style="min-width:0"><h2>{{ siteName(s) }}</h2>
        <div class="dim ng-meta"><span class="mono">{{ listenText(s) }}</span><span>{{ KIND[s.kind] || s.kind }}</span>
          <span v-if="s.ssl" class="pill-tag" :title="s.ssl_certificate || ''"><Icon name="lock" /> TLS</span>
          <span v-if="s.listen.some((l) => l.default)" class="pill-tag">default</span>
          <span class="mono" :title="s.file">{{ short(s.file) }}:{{ s.line }}</span></div></div>
      <div class="ng-nums"><span><b>{{ rate(nums(s.id, "sites")?.req ?? 0) }}</b> req/s</span>
        <span :class="{ bad: (nums(s.id, 'sites')?.e5_pct ?? 0) >= 5 }"><b>{{ (nums(s.id, "sites")?.e5_pct ?? 0).toFixed(1) }}%</b> 5xx</span>
        <span><b>{{ Math.round(nums(s.id, "sites")?.p95 ?? 0) }}</b> ms p95</span></div>
    </div>
    <div class="card-body table-wrap" style="padding-top:0">
      <table v-if="s.context === 'http'" class="data"><thead><tr><th>Route</th><th>Does</th><th>Goes to</th><th>Extras</th><th class="num">Req/s</th><th class="num">5xx</th><th class="num">p95</th></tr></thead>
        <tbody>
          <tr v-if="s.return && !s.locations.length"><td class="mono">(all)</td><td>{{ KIND.redirect }}</td><td class="mono">return {{ s.return }}</td><td /><td class="num">–</td><td class="num">–</td><td class="num">–</td></tr>
          <tr v-for="l in flat(s.locations)" :key="l.id">
            <td class="mono" :style="{ paddingLeft: `${12 + l.depth * 16}px` }">{{ locationText(l) }}</td>
            <td>{{ KIND[l.kind] || l.kind }}</td>
            <td class="mono ng-target">{{ target(l) }}
              <Badge v-if="upstream(l.upstream)" :level="upstreamHealth(upstream(l.upstream)!, d.health).level" :label="upstreamHealth(upstream(l.upstream)!, d.health).label" /></td>
            <td><span v-if="l.websocket" class="pill-tag">WebSocket</span><span v-if="l.limit_req" class="pill-tag" :title="l.limit_req">rate limit</span>
              <span v-if="l.deny.length || l.allow.length" class="pill-tag" :title="[...l.allow.map((a) => `allow ${a}`), ...l.deny.map((x) => `deny ${x}`)].join('; ')">IP rules</span>
              <span v-if="l.auth_basic" class="pill-tag">password</span><span v-if="l.rewrites.length" class="pill-tag" :title="l.rewrites.join('; ')">rewrite</span></td>
            <td class="num">{{ rate(nums(l.id, "locations")?.req ?? 0) }}</td>
            <td class="num" :class="{ bad: (nums(l.id, 'locations')?.e5_pct ?? 0) >= 5 }">{{ (nums(l.id, "locations")?.e5_pct ?? 0).toFixed(1) }}%</td>
            <td class="num">{{ Math.round(nums(l.id, "locations")?.p95 ?? 0) }} ms</td></tr>
        </tbody></table>
      <p v-else class="hint" style="margin:0">TCP/UDP (stream): forwards every connection to <span class="mono">{{ s.proxy_pass || "–" }}</span>.</p>
    </div>
  </section>
</template>

<style scoped>
.ng-site { margin-bottom: 14px; }
.ng-site.focus { outline: 2px solid var(--accent); }
.ng-site-head { align-items: flex-start; gap: 16px; }
.ng-meta { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; font-size: 12.5px; margin-top: 4px; }
.ng-nums { margin-left: auto; display: flex; gap: 16px; font-size: 13px; color: var(--muted); white-space: nowrap; }
.ng-nums b { color: var(--text); font-weight: 650; }
.bad, .bad b { color: var(--critical) !important; }
.ng-target { white-space: normal; overflow-wrap: anywhere; }
</style>
