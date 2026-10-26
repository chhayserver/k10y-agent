<script setup lang="ts">
// Frontends as a dashboard: numbers for what's shown, filters (type, problems, TLS, limits), and each frontend
// as a card or a table row, drawn as its routing: path or rule → backend, with live server health and traffic.
import { computed, reactive, ref, watch } from "vue";
import EmptyState from "@/components/EmptyState.vue";
import Icon from "@/components/Icon.vue";
import RowMenu from "@/components/RowMenu.vue";
import { FRONTEND_KIND, backendHealth, fmtRate, frontendShape, num, serverDots, type FrontendKind, type HxFrontend, type HxOverview, type HxPoint } from "@/lib/haproxy";
import type { MenuItem } from "@/stores/ui";
import HxKpis, { type Kpi } from "./HxKpis.vue";

const props = defineProps<{ data: HxOverview; points: HxPoint[]; focus: string; canConfigure: boolean; instanceId: number; frontendMenu: (f: HxFrontend) => MenuItem[] }>();
const emit = defineEmits<{ newFrontend: []; pick: [kind: string, name: string] }>();

// --- filters (remembered per HAProxy in this browser) --------------------------------------
const KEY = `k10y.hx.frontends.${props.instanceId}`;
let saved: Partial<{ q: string; kind: string; status: string; tags: string[]; view: string; sort: string; open: string[] }> = {};
try { saved = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch { /* storage blocked */ }
const f = reactive({ q: saved.q || "", kind: saved.kind || "all", status: saved.status || "all", tags: saved.tags || [] as string[],
  view: saved.view || "cards", sort: saved.sort || "name" });
const open = ref(new Set<string>(saved.open || []));
watch([f, open], () => { try { localStorage.setItem(KEY, JSON.stringify({ ...f, open: [...open.value] })); } catch { /* */ } }, { deep: true });
watch(() => props.focus, (x) => { if (x.startsWith("f:")) open.value = new Set([...open.value, x.slice(2)]); }, { immediate: true });
const toggle = (n: string) => { const s = new Set(open.value); s.has(n) ? s.delete(n) : s.add(n); open.value = s; };
const toggleTag = (t: string) => { f.tags = f.tags.includes(t) ? f.tags.filter((x) => x !== t) : [...f.tags, t]; };
function clear() { Object.assign(f, { q: "", kind: "all", status: "all", tags: [] }); }
const filtered = computed(() => f.q || f.kind !== "all" || f.status !== "all" || f.tags.length);

// --- rows ------------------------------------------------------------------------------------
const backends = computed(() => new Map(props.data.backends.map((b) => [b.name, b])));
const last = computed(() => props.points[props.points.length - 1]?.[1]);
const all = computed(() => props.data.frontends.map((fe) => {
  const shape = frontendShape(fe, backends.value), live = last.value?.f[fe.name];
  const rate = live?.req ?? (Number(fe.stats.req_rate ?? fe.stats.rate) || 0), err = live?.e5_pct ?? 0;
  const problem = !fe.stats_page && (shape.level !== "good" || err >= 1 || (fe.stats.status && fe.stats.status !== "OPEN"));
  const tls = fe.binds.some((b) => b.ssl);
  return { fe, shape, rate, err, problem, tls, sessions: Number(fe.stats.scur) || 0, e5: Number(fe.stats.hrsp_5xx) || 0,
    spark: props.points.slice(-60).map((p) => p[1].f[fe.name]?.req || 0) };
}));
const counts = computed(() => {
  const c: Record<string, number> = { all: all.value.length, problems: all.value.filter((r) => r.problem).length };
  for (const r of all.value) c[r.shape.kind] = (c[r.shape.kind] || 0) + 1;
  return c;
});
const rows = computed(() => {
  const s = f.q.trim().toLowerCase();
  const list = all.value.filter((r) => (f.kind === "all" || r.shape.kind === f.kind || (f.kind === "rules" && r.shape.kind === "none"))
    && (f.status === "all" || r.problem)
    && (!f.tags.includes("tls") || r.tls) && (!f.tags.includes("limit") || r.fe.policies?.rate_limit) && (!f.tags.includes("allow") || r.fe.policies?.allow_ips?.length)
    && (!s || [r.fe.name, ...r.fe.binds.map((b) => `${b.address}:${b.port}`), ...r.shape.lanes.flatMap((l) => [l.match, l.backend])].some((x) => x.toLowerCase().includes(s))));
  const key: Record<string, (r: (typeof list)[number]) => number | string> = { name: (r) => r.fe.name, rate: (r) => -r.rate, err: (r) => -r.err, sessions: (r) => -r.sessions };
  const k = key[f.sort] || key.name;
  return list.sort((a, b) => (Number(b.problem) - Number(a.problem)) || (typeof k(a) === "number" ? (k(a) as number) - (k(b) as number) : String(k(a)).localeCompare(String(k(b)))));
});

// --- numbers for what's shown ----------------------------------------------------------------
const kpis = computed<Kpi[]>(() => {
  const r = rows.value.filter((x) => !x.fe.stats_page), names = r.map((x) => x.fe.name);
  const sumAt = (p: HxPoint[1], k: "req" | "e5" | "sess") => names.reduce((a, n) => a + (p.f[n]?.[k] || 0), 0);
  const pts = props.points.slice(-60).map((p) => p[1]);
  const req = r.reduce((a, x) => a + x.rate, 0), e5 = last.value ? sumAt(last.value, "e5") : 0, pct = req ? (100 * e5) / req : 0;
  const bes = [...new Set(r.flatMap((x) => x.shape.backends))].map((n) => backends.value.get(n));
  const ok = bes.filter((b) => b && backendHealth(b).level === "good").length, bad = bes.filter((b) => !b || backendHealth(b).level === "critical").length;
  const kinds = (["proxy", "paths", "rules"] as FrontendKind[]).map((k) => [k, r.filter((x) => x.shape.kind === k).length] as const).filter(([, n]) => n);
  return [
    { k: "fe", label: filtered.value ? "Frontends shown" : "Frontends", value: String(r.length), of: filtered.value ? all.value.filter((x) => !x.fe.stats_page).length : undefined,
      sub: kinds.map(([k, n]) => `${n} ${k === "rules" ? "other rules" : FRONTEND_KIND[k].toLowerCase()}`).join(" · ") || "none" },
    { k: "req", label: "Requests / s", value: fmtRate(req), sub: "last 10 seconds", spark: pts.map((p) => sumAt(p, "req")) },
    { k: "err", label: "Error rate", value: `${pct < 10 ? pct.toFixed(1) : pct.toFixed(0)}%`, sub: "5xx share of responses", spark: pts.map((p) => { const q = sumAt(p, "req"); return q ? (100 * sumAt(p, "e5")) / q : 0; }), level: pct >= 5 ? "critical" : pct >= 1 ? "warning" : "" },
    { k: "sess", label: "Sessions", value: num(r.reduce((a, x) => a + x.sessions, 0)), sub: "open right now", spark: pts.map((p) => sumAt(p, "sess")) },
    { k: "be", label: "Backends healthy", value: String(ok), of: bes.length, sub: bad ? `${bad} with no server up` : bes.length - ok ? `${bes.length - ok} partly down` : "all servers up",
      bar: { up: ok, out: bes.length - ok - bad, down: bad }, level: bad ? "critical" : bes.length - ok ? "warning" : "" },
  ];
});
const KINDS: [string, string][] = [["all", "All"], ["proxy", "Proxy"], ["paths", "Path routing"], ["rules", "Other rules"]];
const TAGS: [string, string][] = [["tls", "TLS"], ["limit", "Rate limit"], ["allow", "IP allow-list"]];
const listen = (fe: HxFrontend) => fe.binds.map((b) => ({ text: `${b.address === "*" ? "" : b.address}:${b.port}`, ssl: b.ssl }));
const errClass = (e: number) => (e >= 5 ? "bad" : e >= 1 ? "warn" : "");
</script>

<template>
  <HxKpis :items="kpis" />

  <div class="toolbar hxf-bar">
    <label class="search"><Icon name="search" /><input v-model="f.q" class="input" placeholder="Search name, port, path or backend" aria-label="Search"></label>
    <div class="segmented" role="group" aria-label="Type">
      <button v-for="[k, label] in KINDS" :key="k" :aria-pressed="f.kind === k" @click="f.kind = k">{{ label }}<span class="hxf-n">{{ k === "rules" ? (counts.rules || 0) + (counts.none || 0) : counts[k] || 0 }}</span></button></div>
    <div class="segmented" role="group" aria-label="Status">
      <button :aria-pressed="f.status === 'all'" @click="f.status = 'all'">Any status</button>
      <button :aria-pressed="f.status === 'problems'" @click="f.status = 'problems'">Problems<span v-if="counts.problems" class="count-pill">{{ counts.problems }}</span></button></div>
    <span class="grow" />
    <button v-if="canConfigure" class="btn primary" @click="emit('newFrontend')"><Icon name="upload" /><span>New frontend</span></button>
  </div>
  <div class="hxf-bar2">
    <span class="hint">Only with</span>
    <button v-for="[k, label] in TAGS" :key="k" class="hxf-tag" :aria-pressed="f.tags.includes(k)" @click="toggleTag(k)">{{ label }}</button>
    <button v-if="filtered" class="link" @click="clear">Clear filters</button>
    <span class="grow" />
    <label class="hint" for="hxf-sort">Sort</label>
    <select id="hxf-sort" v-model="f.sort" class="select sm"><option value="name">Name</option><option value="rate">Most traffic</option><option value="err">Most errors</option><option value="sessions">Most sessions</option></select>
    <div class="segmented sm" role="group" aria-label="View">
      <button :aria-pressed="f.view === 'cards'" @click="f.view = 'cards'">Cards</button><button :aria-pressed="f.view === 'table'" @click="f.view = 'table'">Table</button></div>
  </div>

  <EmptyState v-if="!rows.length" :title="all.length ? 'No frontends match' : 'No frontends yet'" icon="globe" class="card"
    :sub="all.length ? 'Change the search or the filters.' : 'A frontend is the address clients connect to, and which backend gets each request.'" />

  <!-- cards -->
  <div v-else-if="f.view === 'cards'" class="hxf-cards">
    <article v-for="r in rows" :id="`f:${r.fe.name}`" :key="r.fe.name" class="card hxf-card" :class="[r.problem ? r.shape.level : '', { 'hx-focus': focus === `f:${r.fe.name}` }]">
      <header class="hxf-head">
        <div class="hxf-title"><span class="mono">{{ r.fe.name }}</span><span class="chip"><Icon :name="r.shape.icon" />{{ r.shape.label }}</span>
          <span class="grow" /><RowMenu :items="() => frontendMenu(r.fe)" :label="r.fe.name" /></div>
        <div class="hxf-listen"><span v-for="b in listen(r.fe)" :key="b.text" class="mono"><Icon v-if="b.ssl" name="lock" />{{ b.text }}</span>
          <span class="dim">{{ r.fe.mode.toUpperCase() }}</span>
          <span v-if="r.fe.policies?.redirect_https" class="chip sm">→ HTTPS</span>
          <span v-if="r.fe.policies?.rate_limit" class="chip sm">≤ {{ r.fe.policies.rate_limit.requests }}/{{ r.fe.policies.rate_limit.period }}s per IP</span>
          <span v-if="r.fe.policies?.allow_ips?.length" class="chip sm" :title="r.fe.policies.allow_ips.join(' ')">IP allow-list ({{ r.fe.policies.allow_ips.length }})</span></div>
        <div class="hxf-nums">
          <span><b>{{ fmtRate(r.rate) }}</b> req/s</span><span :class="errClass(r.err)"><b>{{ fmtRate(r.err) }}%</b> errors</span>
          <span><b>{{ num(r.sessions) }}</b> sessions</span>
          <svg v-if="r.spark.length > 1" class="hxf-spark" viewBox="0 0 100 20" preserveAspectRatio="none" aria-hidden="true">
            <path :d="r.spark.map((v, i) => `${i ? 'L' : 'M'}${(i * 100 / (r.spark.length - 1)).toFixed(1)},${(19 - (v / Math.max(...r.spark, 1e-9)) * 17).toFixed(1)}`).join(' ')" /></svg></div>
      </header>
      <ul class="hxf-lanes">
        <li v-for="(l, i) in r.shape.lanes" :key="i" class="clickable" @click="emit('pick', 'backend', l.backend)">
          <span class="hxf-match"><span v-if="l.kind" class="dim">{{ l.kind }}</span> <code>{{ l.match }}</code></span><span class="hxf-arrow">→</span>
          <span class="hxf-be"><span class="mono">{{ l.backend }}</span><span class="hxf-dots"><i v-for="(s, j) in serverDots(backends.get(l.backend))" :key="j" :class="s" /></span>
            <span v-if="!backends.get(l.backend)" class="chip sm bad">missing</span></span>
          <span class="hxf-rate dim">{{ fmtRate(last?.b[l.backend]?.req || 0) }}/s</span></li>
        <li v-if="r.fe.stats_page" class="dim">HAProxy statistics page</li>
        <li v-else-if="!r.shape.lanes.length" class="dim">No backend: every request gets a 503.</li>
        <li v-if="!r.fe.simple_routes" class="dim">Also has custom routing rules (see Raw config).</li>
      </ul>
    </article>
  </div>

  <!-- table -->
  <section v-else class="card flush"><div class="card-body table-wrap">
    <table class="data hxf-table">
      <thead><tr><th class="hxf-caret" /><th>Frontend</th><th>Listen</th><th>Goes to</th><th class="num">Req/s</th><th class="num">Errors</th><th class="num">Sessions</th><th class="actions"><span class="sr-only">Actions</span></th></tr></thead>
      <tbody v-for="r in rows" :id="`f:${r.fe.name}`" :key="r.fe.name" :class="{ 'hx-focus': focus === `f:${r.fe.name}`, open: open.has(r.fe.name) }">
        <tr class="hxf-row" :class="r.problem ? r.shape.level : ''" tabindex="0" :aria-expanded="open.has(r.fe.name)"
          @click="(e) => !(e.target as HTMLElement).closest('button, a') && toggle(r.fe.name)" @keydown.enter.self.prevent="toggle(r.fe.name)">
          <td class="hxf-caret"><Icon name="sort" :class="{ turned: open.has(r.fe.name) }" /></td>
          <td class="primary"><span class="mono">{{ r.fe.name }}</span><div class="sub">{{ r.shape.label }}</div></td>
          <td><span v-for="b in listen(r.fe)" :key="b.text" class="mono hxf-bind"><Icon v-if="b.ssl" name="lock" />{{ b.text }}</span></td>
          <td><span v-for="n in r.shape.backends" :key="n" class="hxf-be-pill"><span class="mono">{{ n }}</span><span class="hxf-dots"><i v-for="(s, j) in serverDots(backends.get(n))" :key="j" :class="s" /></span></span>
            <span v-if="!r.shape.backends.length" class="dim">{{ r.fe.stats_page ? "stats page" : "none" }}</span></td>
          <td class="num">{{ fmtRate(r.rate) }}</td><td class="num" :class="errClass(r.err)">{{ fmtRate(r.err) }}%</td><td class="num">{{ num(r.sessions) }}</td>
          <td class="actions"><RowMenu :items="() => frontendMenu(r.fe)" :label="r.fe.name" /></td>
        </tr>
        <tr v-if="open.has(r.fe.name)" class="hxf-detail"><td /><td colspan="7">
          <ul class="hxf-lanes flat">
            <li v-for="(l, i) in r.shape.lanes" :key="i" class="clickable" @click="emit('pick', 'backend', l.backend)">
              <span class="hxf-match"><span v-if="l.kind" class="dim">{{ l.kind }}</span> <code>{{ l.match }}</code></span><span class="hxf-arrow">→</span>
              <span class="hxf-be"><span class="mono">{{ l.backend }}</span><span class="hxf-dots"><i v-for="(s, j) in serverDots(backends.get(l.backend))" :key="j" :class="s" /></span></span>
              <span class="hxf-rate dim">{{ fmtRate(last?.b[l.backend]?.req || 0) }}/s</span></li>
            <li v-if="!r.shape.lanes.length" class="dim">{{ r.fe.stats_page ? "HAProxy statistics page" : "No backend: every request gets a 503." }}</li>
          </ul>
          <div class="hxf-pol"><span v-if="r.fe.policies?.redirect_https" class="chip sm">→ HTTPS</span>
            <span v-if="r.fe.policies?.rate_limit" class="chip sm">≤ {{ r.fe.policies.rate_limit.requests }}/{{ r.fe.policies.rate_limit.period }}s per IP</span>
            <span v-if="r.fe.policies?.allow_ips?.length" class="chip sm">IP allow-list: {{ r.fe.policies.allow_ips.join(", ") }}</span>
            <span class="dim">{{ num(r.e5) }} server errors since HAProxy started</span></div>
        </td></tr>
      </tbody>
    </table>
  </div></section>
</template>

<style scoped>
.grow { flex: 1; }
.hxf-bar { margin-top: 16px; }
.hxf-n { margin-left: 6px; font-size: 11.5px; color: var(--muted); font-variant-numeric: tabular-nums; }
.hxf-bar2 { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin: -4px 0 14px; }
.hxf-tag { padding: 3px 11px; border: 1px solid var(--border); border-radius: 999px; background: none; color: var(--text-2); font: inherit; font-size: 12.5px; cursor: pointer; }
.hxf-tag:hover { background: var(--surface-hover); }
.hxf-tag[aria-pressed="true"] { background: var(--surface-active); color: var(--text); border-color: var(--accent); }
.select.sm { height: 30px; padding: 0 8px; font-size: 13px; width: auto; }

.hxf-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(440px, 1fr)); gap: 12px; }
@media (max-width: 760px) { .hxf-cards { grid-template-columns: 1fr; } }
.hxf-card { padding: 0; overflow: hidden; }
.hxf-card.critical { border-color: color-mix(in srgb, var(--critical) 50%, var(--border)); }
.hxf-card.warning { border-color: color-mix(in srgb, var(--warning) 50%, var(--border)); }
.hxf-head { display: flex; flex-direction: column; gap: 7px; padding: 12px 12px 12px 16px; border-bottom: 1px solid var(--border); }
.hxf-title { display: flex; align-items: center; gap: 10px; }
.hxf-title .mono { font-size: 15px; font-weight: 650; }
.chip .icon { width: 12px; height: 12px; margin-right: 4px; }
.chip.bad { color: var(--critical); }
.hxf-listen { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; font-size: 12.5px; color: var(--text-2); }
.hxf-listen .icon, .hxf-bind .icon { width: 12px; height: 12px; margin-right: 3px; vertical-align: -1px; color: var(--good); }
.hxf-nums { display: flex; gap: 16px; align-items: center; flex-wrap: wrap; font-size: 12.5px; color: var(--muted); }
.hxf-nums b { color: var(--text); font-variant-numeric: tabular-nums; }
.warn b, td.warn { color: var(--warning); } .bad b, td.bad { color: var(--critical); }
.hxf-spark { flex: 1; min-width: 60px; max-width: 160px; height: 20px; margin-left: auto; }
.hxf-spark path { fill: none; stroke: var(--accent); stroke-width: 1.4; vector-effect: non-scaling-stroke; }
.hxf-lanes { list-style: none; margin: 0; padding: 6px 0; }
.hxf-lanes li { display: grid; grid-template-columns: minmax(0, 1.1fr) 18px minmax(0, 1fr) 56px; align-items: center; gap: 8px; padding: 7px 16px; font-size: 13px; }
.hxf-lanes.flat li { padding: 6px 8px; }
.hxf-lanes li.clickable { cursor: pointer; } .hxf-lanes li.clickable:hover { background: var(--surface-hover); }
.hxf-match { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hxf-arrow { color: var(--muted); text-align: center; }
.hxf-be { display: flex; align-items: center; gap: 8px; min-width: 0; }
.hxf-be .mono { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hxf-dots { display: inline-flex; gap: 3px; align-items: center; flex-wrap: wrap; }
.hxf-dots i { width: 8px; height: 8px; border-radius: 50%; display: block; }
.hxf-dots .up { background: var(--good); } .hxf-dots .out { background: var(--warning); } .hxf-dots .down { background: var(--critical); }
.hxf-rate { text-align: right; font-variant-numeric: tabular-nums; font-size: 12px; }

.hxf-caret { width: 30px; padding-right: 0 !important; }
.hxf-caret .icon { width: 14px; height: 14px; color: var(--muted); transform: rotate(90deg); transition: transform .15s; }
.hxf-caret .icon.turned { transform: rotate(180deg); color: var(--text); }
.hxf-row { cursor: pointer; }
.hxf-row:hover td { background: var(--surface-hover); }
.hxf-row.critical td:first-child { box-shadow: inset 3px 0 0 var(--critical); }
.hxf-row.warning td:first-child { box-shadow: inset 3px 0 0 var(--warning); }
table.hxf-table tbody + tbody { border-top: 1px solid var(--border); }
tbody.open .hxf-row td { background: var(--surface-active); }
.sub { font-size: 12px; color: var(--muted); margin-top: 2px; font-weight: 400; }
.hxf-bind { display: block; }
.hxf-be-pill { display: inline-flex; align-items: center; gap: 6px; margin: 2px 12px 2px 0; }
.hxf-detail td { background: color-mix(in srgb, var(--surface-active) 45%, transparent); padding-top: 4px !important; padding-bottom: 10px !important; }
.hxf-pol { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; padding: 4px 8px 0; font-size: 12.5px; }
</style>
