<script setup lang="ts">
// Nginx at a glance: key numbers with trends, problems, traffic over the last hour, and every site and upstream.
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import Badge from "@/components/Badge.vue";
import Icon from "@/components/Icon.vue";
import LineChart from "@/components/LineChart.vue";
import { to } from "@/lib/nav";
import { KIND, listenText, rate, serverKey, siteName, upstreamHealth } from "@/lib/nginx";
import { useNginx } from "@/stores/nginx";
import { toast } from "@/stores/ui";
import HxKpis from "../haproxy/HxKpis.vue";

const emit = defineEmits<{ focus: [key: string] }>();
const n = useNginx(), router = useRouter();
const d = computed(() => n.data!);
const last = computed(() => n.last);
const pts = computed(() => n.points);
const servers = computed(() => d.value.config.upstreams.flatMap((u) => u.servers.filter((s) => !s.down)));
const upCount = computed(() => servers.value.filter((s) => d.value.health[serverKey(s)]?.up).length);
const downCount = computed(() => servers.value.filter((s) => d.value.health[serverKey(s)]?.up === false).length);
const kpis = computed(() => [
  { k: "req", label: "Requests / s", value: rate(last.value?.req ?? d.value.summary.req), sub: "from the access log", spark: pts.value.map((p) => p[1].req) },
  { k: "e5", label: "Server errors (5xx)", value: `${(last.value?.e5_pct ?? 0).toFixed(1)}%`, sub: "of responses", spark: pts.value.map((p) => p[1].e5_pct),
    level: (last.value?.e5_pct ?? 0) >= 5 ? "critical" : (last.value?.e5_pct ?? 0) >= 1 ? "warning" : "" },
  { k: "p95", label: "Response time", value: `${Math.round(last.value?.p95 ?? 0)} ms`, sub: "95th percentile", spark: pts.value.map((p) => p[1].p95) },
  { k: "conn", label: "Connections", value: d.value.summary.active == null ? "–" : String(d.value.summary.active),
    sub: d.value.info.stub_status ? "open now (stub_status)" : "turn on stub_status to see this" },
  { k: "servers", label: "Upstream servers", value: String(upCount.value), of: servers.value.length, sub: downCount.value ? `${downCount.value} not answering` : "answering",
    bar: { up: upCount.value, out: servers.value.length - upCount.value - downCount.value, down: downCount.value }, level: downCount.value ? "critical" : "" },
]);
const range = ref(15 * 60e3);
const series = computed(() => [
  { label: "Requests/s", color: "var(--accent)", points: pts.value.map((p) => [p[0], p[1].req] as [number, number]) },
  { label: "5xx/s", color: "var(--critical)", points: pts.value.map((p) => [p[0], (p[1].req * p[1].e5_pct) / 100] as [number, number]) },
]);
const logFormat = computed(() => Object.values(d.value.info.log_formats || {})[0] || (d.value.info.access_logs.length ? "waiting for requests" : "none"));
const needsFormat = computed(() => logFormat.value !== "k10y");
async function copyFormat() {
  try { await navigator.clipboard.writeText(d.value.info.recommended_log_format); toast(true, "Copied the log_format line"); } catch { toast(false, "Copy failed"); }
}
function goSite(id: string) { emit("focus", id); router.push(to("nginx-sites")); }
function goUpstream(name: string) { emit("focus", name); router.push(to("nginx-upstreams")); }
</script>

<template>
  <div class="ng-status">
    <Badge :level="d.info.running ? (d.summary.problems.length ? 'warning' : 'good') : 'critical'"
      :label="!d.info.running ? 'Nginx not running' : d.summary.problems.length ? `${d.summary.problems.length} problem${d.summary.problems.length > 1 ? 's' : ''}` : 'Healthy'" lg />
    <span class="dim">Nginx {{ d.info.nginx_version || "?" }} · agent {{ d.info.agent_version }} · {{ d.config.files.length }} config file{{ d.config.files.length > 1 ? "s" : "" }}</span>
  </div>
  <HxKpis :items="kpis" />

  <section v-if="d.summary.problems.length" class="card ng-problems"><div class="card-head"><h2>Problems</h2></div><div class="card-body">
    <div v-for="(p, i) in d.summary.problems" :key="i" class="ng-problem"><Icon :name="p.kind.includes('down') || p.kind === 'not_running' ? 'crit' : 'warn'" />
      <span>{{ p.message }}</span>
      <button v-if="p.upstream" class="btn ghost sm" type="button" @click="goUpstream(p.upstream)">Open</button></div>
  </div></section>

  <div v-if="needsFormat" class="banner show info ng-tip"><Icon name="warn" /><span class="msg">
    Access log format: <b>{{ logFormat }}</b>. For numbers per site, route and upstream server, log in k10y's format:
    add the <code>log_format k10y …</code> line to <code>http {}</code> and use <code>access_log &lt;file&gt; k10y;</code>.</span>
    <button class="btn sm" type="button" @click="copyFormat"><Icon name="copy" /><span>Copy log_format</span></button></div>

  <section class="card"><div class="card-head"><h2>Traffic</h2><div class="right"><div class="segmented sm" role="group">
    <button v-for="[m, l] in [[5, '5 min'], [15, '15 min'], [60, '1 hour']]" :key="m" type="button" :aria-pressed="range === Number(m) * 60e3" @click="range = Number(m) * 60e3">{{ l }}</button></div></div></div>
    <div class="card-body"><LineChart :series="series" :y-fmt="(v: number) => rate(v)" :range="range" :height="180" /></div></section>

  <div class="two-col ng-lists">
    <section class="card flush"><div class="card-head"><h2>Sites</h2><div class="right"><RouterLink class="btn ghost sm" :to="to('nginx-sites')">All sites</RouterLink></div></div>
      <div class="card-body table-wrap"><table class="data"><thead><tr><th>Site</th><th>Listens</th><th>Type</th><th class="num">Req/s</th><th class="num">5xx</th></tr></thead>
        <tbody><tr v-for="s in d.config.sites" :key="s.id" class="clickable" @click="goSite(s.id)">
          <td class="primary">{{ siteName(s) }}</td><td class="mono dim">{{ listenText(s) }}</td><td>{{ KIND[s.kind] || s.kind }}</td>
          <td class="num">{{ rate(last?.sites?.[s.id]?.req ?? 0) }}</td>
          <td class="num" :style="{ color: (last?.sites?.[s.id]?.e5_pct ?? 0) >= 5 ? 'var(--critical)' : '' }">{{ (last?.sites?.[s.id]?.e5_pct ?? 0).toFixed(1) }}%</td></tr></tbody></table></div></section>
    <section class="card flush"><div class="card-head"><h2>Upstreams</h2><div class="right"><RouterLink class="btn ghost sm" :to="to('nginx-upstreams')">All upstreams</RouterLink></div></div>
      <div class="card-body table-wrap"><table class="data"><thead><tr><th>Upstream</th><th>Servers</th><th>Balancing</th><th class="num">Req/s</th></tr></thead>
        <tbody><tr v-for="u in d.config.upstreams" :key="u.id" class="clickable" @click="goUpstream(u.name)">
          <td class="primary mono">{{ u.name }}<span v-if="u.context === 'stream'" class="pill-tag">TCP</span></td>
          <td><Badge :level="upstreamHealth(u, d.health).level" :label="upstreamHealth(u, d.health).label" /></td>
          <td class="dim">{{ u.method.replace("_", " ") }}</td><td class="num">{{ rate(last?.upstreams?.[u.name]?.req ?? 0) }}</td></tr>
          <tr v-if="!d.config.upstreams.length"><td colspan="4" class="dim">No upstream blocks (sites may proxy to addresses directly).</td></tr></tbody></table></div></section>
  </div>
</template>

<style scoped>
.ng-status { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 14px; }
.ng-problems { margin: 16px 0; }
.ng-problem { display: flex; align-items: center; gap: 10px; padding: 6px 0; font-size: 14px; }
.ng-problem span { flex: 1; }
.ng-tip { margin: 16px 0; align-items: center; }
.ng-tip .msg { flex: 1; }
.ng-lists { margin-top: 16px; }
</style>
