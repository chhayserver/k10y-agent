<script setup lang="ts">
// HAProxy dashboard: one status line, key numbers, problems, each frontend drawn as its routing
// (proxy or path routing), one flexible traffic chart and an optional topology. Layout is per user.
import { computed, ref } from "vue";
import Badge from "@/components/Badge.vue";
import EmptyState from "@/components/EmptyState.vue";
import Icon from "@/components/Icon.vue";
import LineChart from "@/components/LineChart.vue";
import { backendHealth, fmtRate as fmtN, frontendShape, num, serverDots as dots, type HxFrontend, type HxOverview, type HxPoint } from "@/lib/haproxy";
import { prefs, resetHxDash } from "@/stores/prefs";
import HxKpis, { type Kpi } from "./HxKpis.vue";
import HxTopology from "./HxTopology.vue";

const props = defineProps<{ data: HxOverview; points: HxPoint[]; canAi: boolean; canManage: boolean }>();
const emit = defineEmits<{ pick: [kind: string, name: string]; ai: []; go: [page: string]; connection: []; addHaproxy: [] }>();
const d = computed(() => prefs.hxDash);
const on = (k: string) => d.value.sections[k] !== false;

// --- numbers -----------------------------------------------------------------------------
const frontends = computed(() => props.data.frontends.filter((f) => !f.stats_page));
const backendsBy = computed(() => new Map(props.data.backends.map((b) => [b.name, b])));
const last = computed(() => props.points[props.points.length - 1]?.[1]);
const sum = (o: Record<string, any> | undefined, k: string) => Object.values(o || {}).reduce((a, v: any) => a + (Number(v[k]) || 0), 0);
const trend = (fn: (p: HxPoint[1]) => number) => props.points.slice(-60).map((p) => fn(p[1]));
const errPct = (p: HxPoint[1] | undefined) => { const r = sum(p?.f, "req"); return r ? (100 * sum(p?.f, "e5")) / r : 0; };
const rtimeAvg = (p: HxPoint[1] | undefined) => { const v = Object.values(p?.b || {}).map((x) => x.rtime).filter((x) => x > 0); return v.length ? v.reduce((a, x) => a + x, 0) / v.length : 0; };
const servers = computed(() => props.data.backends.flatMap((b) => b.servers));
const kpis = computed<(Kpi & { page: string })[]>(() => {
  const up = servers.value.filter((x) => x.state === "up" || x.state === "nocheck").length;
  const down = servers.value.filter((x) => x.state === "down").length, out = servers.value.filter((x) => x.state === "drain" || x.state === "maint").length;
  const req = last.value ? sum(last.value.f, "req") : frontends.value.reduce((a, f) => a + (Number(f.stats.req_rate ?? f.stats.rate) || 0), 0);
  const e = errPct(last.value), rt = rtimeAvg(last.value);
  return [
    { k: "req", label: "Requests / s", value: fmtN(req), sub: "all frontends, last 10 s", spark: trend((p) => sum(p.f, "req")), page: "haproxy-frontends" },
    { k: "err", label: "Error rate", value: `${e < 10 ? e.toFixed(1) : e.toFixed(0)}%`, sub: "5xx share of responses", spark: trend(errPct), level: e >= 5 ? "critical" : e >= 1 ? "warning" : "", page: "haproxy-logs" },
    { k: "rt", label: "Response time", value: rt ? `${Math.round(rt)} ms` : "–", sub: "average over backends", spark: trend(rtimeAvg), page: "haproxy-backends" },
    { k: "sess", label: "Sessions", value: num(sum(last.value?.f, "sess") || frontends.value.reduce((a, f) => a + (Number(f.stats.scur) || 0), 0)), sub: "open right now", spark: trend((p) => sum(p.f, "sess")), page: "haproxy-frontends" },
    { k: "srv", label: "Servers up", value: `${up}`, of: servers.value.length, sub: down || out ? `${down} down · ${out} out of rotation` : "all in rotation", bar: { up, out, down }, level: down ? "critical" : out ? "warning" : "", page: "haproxy-backends" },
  ];
});

// --- problems and overall status ------------------------------------------------------------
const problems = computed(() => {
  const out: { level: "critical" | "warning"; text: string; detail: string; backend: string }[] = [];
  for (const b of props.data.backends) {
    const h = backendHealth(b);
    if (h.total && h.up === 0) { out.push({ level: b.used_by.length ? "critical" : "warning", text: `${b.name} has no server up`, detail: b.used_by.length ? `Requests to ${b.used_by.join(", ")} fail` : "Not used by any frontend", backend: b.name }); continue; }
    for (const sv of b.servers) if (sv.state === "down")
      out.push({ level: "warning", text: `${b.name}/${sv.name} is down`, detail: sv.stats.check_desc || sv.stats.check_status || "Health check failing", backend: b.name });
  }
  const e = errPct(last.value);
  if (e >= 5) out.push({ level: "warning", text: `${e.toFixed(0)}% of responses are server errors`, detail: "Check Logs for the failing requests", backend: "" });
  return out.sort((a, b) => (a.level === b.level ? 0 : a.level === "critical" ? -1 : 1));
});
const status = computed(() => {
  const p = problems.value;
  if (p.some((x) => x.level === "critical")) return { level: "critical", icon: "crit", text: `${p.length} problem${p.length > 1 ? "s" : ""} need${p.length > 1 ? "" : "s"} attention` };
  if (p.length) return { level: "warning", icon: "warn", text: `${p.length} warning${p.length > 1 ? "s" : ""}` };
  return { level: "good", icon: "ok", text: "Everything is healthy" };
});

// --- frontends drawn as their routing ------------------------------------------------------
const shape = (f: HxFrontend) => frontendShape(f, backendsBy.value);
const feRate = (f: HxFrontend) => last.value?.f[f.name]?.req ?? (Number(f.stats.req_rate ?? f.stats.rate) || 0);
const feErr = (f: HxFrontend) => last.value?.f[f.name]?.e5_pct ?? 0;
const beRate = (name: string) => last.value?.b[name]?.req ?? 0;

// --- traffic chart ---------------------------------------------------------------------------
const METRICS: Record<string, { label: string; field: string; fmt: (v: number) => string; backendOnly?: boolean }> = {
  req: { label: "Requests / s", field: "req", fmt: fmtN },
  e5: { label: "Errors (5xx) / s", field: "e5", fmt: fmtN },
  e5_pct: { label: "Error rate %", field: "e5_pct", fmt: (v) => `${fmtN(v)}%` },
  sess: { label: "Sessions", field: "sess", fmt: fmtN },
  rtime: { label: "Response time", field: "rtime", fmt: (v) => `${Math.round(v)} ms`, backendOnly: true },
};
const COLORS = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)", "var(--series-5)", "var(--series-6)", "var(--series-7)", "var(--series-8)"];
const by = computed(() => (METRICS[d.value.metric]?.backendOnly ? "backend" : d.value.by));
const names = computed(() => (by.value === "frontend" ? frontends.value.map((f) => f.name) : props.data.backends.map((b) => b.name)));
const chart = computed(() => {
  const m = METRICS[d.value.metric] || METRICS.req, key = by.value === "frontend" ? "f" : "b";
  const pick = d.value.only.filter((n) => names.value.includes(n));
  return names.value.map((n, i) => ({ n, color: COLORS[i % COLORS.length] })).filter((x) => !pick.length || pick.includes(x.n)).map(({ n, color }) => ({
    label: n, color,
    points: props.points.filter((p) => (p[1] as any)[key][n]).map((p) => {
      const v: any = (p[1] as any)[key][n];
      const y = m.field === "e5_pct" && key === "b" ? (v.req ? (100 * v.e5) / v.req : 0) : Number(v[m.field]) || 0;
      return [p[0], y] as [number, number];
    }),
  }));
});
function toggleOnly(n: string) {
  const cur = d.value.only.filter((x) => names.value.includes(x));
  d.value.only = cur.includes(n) ? cur.filter((x) => x !== n) : [...cur, n];
}
const colorOf = (n: string) => COLORS[names.value.indexOf(n) % COLORS.length];

// --- customize -------------------------------------------------------------------------------
const SECTIONS: [string, string, string][] = [["kpis", "Key numbers", "Requests, errors, response time, sessions, servers"],
  ["problems", "Problems", "Only shows when something is wrong"], ["frontends", "Frontends", "Each frontend with its routes and backends"],
  ["traffic", "Traffic chart", "One chart; choose the metric and what to compare"], ["topology", "Topology", "Frontends → backends → servers diagram"]];
const customizing = ref(false);
</script>

<template>
  <!-- status line -->
  <div class="hxd-status" :class="status.level">
    <span class="hxd-status-ic"><Icon :name="status.icon" /></span>
    <div class="hxd-status-text"><b>{{ status.text }}</b>
      <span class="hint">{{ data.instance.name }} · {{ data.instance.url }} · config version {{ data.config_version }}<template v-if="data.instance.group_name"> · group {{ data.instance.group_name }} ({{ data.group.length + 1 }} HAProxy)</template></span></div>
    <span class="grow" />
    <button v-if="canAi" class="btn" @click="emit('ai')"><Icon name="ai" /><span>{{ problems.length ? "Ask AI why" : "Ask AI" }}</span></button>
    <button class="btn" :aria-pressed="customizing" @click="customizing = !customizing"><Icon name="gear" /><span>Customize</span></button>
    <button v-if="canManage" class="btn ghost icon-only" title="Connection settings" aria-label="Connection settings" @click="emit('connection')"><Icon name="lb" /></button>
  </div>

  <!-- customize panel -->
  <section v-if="customizing" class="card hxd-custom"><div class="card-body">
    <div class="hxd-custom-head"><b>Show on this dashboard</b><span class="hint">Saved to your account.</span><span class="grow" />
      <button class="link" @click="resetHxDash()">Reset layout</button><button class="btn sm" @click="customizing = false">Done</button></div>
    <div class="hxd-custom-grid">
      <label v-for="[k, label, hint] in SECTIONS" :key="k" class="hxd-check"><input v-model="d.sections[k]" type="checkbox">
        <span><b>{{ label }}</b><span class="hint">{{ hint }}</span></span></label></div>
    <div v-if="canManage" class="hxd-custom-foot"><button class="link" @click="emit('addHaproxy')">+ Add another HAProxy</button></div>
  </div></section>

  <!-- key numbers -->
  <HxKpis v-if="on('kpis')" :items="kpis" clickable style="margin-top:16px" @pick="(k) => emit('go', kpis.find((x) => x.k === k)!.page)" />

  <!-- problems: only when there are some -->
  <section v-if="on('problems') && problems.length" class="card hxd-section"><div class="card-head"><h2>Needs attention</h2><span class="count-pill">{{ problems.length }}</span></div>
    <ul class="list"><li v-for="p in problems" :key="p.text" :class="{ clickable: p.backend }" @click="p.backend ? emit('pick', 'backend', p.backend) : emit('go', 'haproxy-logs')">
      <span class="ic"><Icon :name="p.level === 'critical' ? 'crit' : 'warn'" :style="{ color: p.level === 'critical' ? 'var(--critical)' : 'var(--warning)' }" /></span>
      <div><div class="main-line">{{ p.text }}</div><div class="sub-line">{{ p.detail }}</div></div>
      <span class="hxd-go">{{ p.backend ? "Open backend" : "Open logs" }} →</span></li></ul>
  </section>

  <!-- frontends with their routing -->
  <section v-if="on('frontends')" class="hxd-section">
    <div class="hxd-head"><h2>Frontends</h2><span class="hint">Where clients connect, and where each request goes</span><span class="grow" />
      <button class="link" @click="emit('go', 'haproxy-frontends')">Manage frontends →</button></div>
    <EmptyState v-if="!frontends.length" title="No frontends yet" sub="Create a frontend on the Frontends page to start receiving traffic." icon="globe" />
    <div v-else class="hxd-fes">
      <article v-for="f in frontends" :key="f.name" class="card hxd-fe">
        <header class="hxd-fe-head" role="button" tabindex="0" @click="emit('pick', 'frontend', f.name)" @keydown.enter="emit('pick', 'frontend', f.name)">
          <div class="hxd-fe-title"><span class="mono">{{ f.name }}</span><span class="chip"><Icon :name="shape(f).icon" />{{ shape(f).label }}</span></div>
          <div class="hxd-fe-listen">
            <span v-for="b in f.binds" :key="`${b.address}:${b.port}`" class="mono"><Icon v-if="b.ssl" name="lock" />{{ b.address === "*" ? "" : b.address }}:{{ b.port }}</span>
            <span v-if="f.policies?.rate_limit" class="chip sm">rate limit</span><span v-if="f.policies?.allow_ips?.length" class="chip sm">IP allow-list</span></div>
          <div class="hxd-fe-nums"><span><b>{{ fmtN(feRate(f)) }}</b> req/s</span>
            <span :class="{ bad: feErr(f) >= 5, warn: feErr(f) >= 1 && feErr(f) < 5 }"><b>{{ fmtN(feErr(f)) }}%</b> errors</span>
            <span><b>{{ num(f.stats.scur) }}</b> sessions</span></div>
        </header>
        <ul class="hxd-lanes">
          <li v-for="(l, i) in shape(f).lanes" :key="i" class="clickable" @click="emit('pick', 'backend', l.backend)">
            <span class="hxd-match"><span v-if="l.kind" class="dim">{{ l.kind }}</span> <code>{{ l.match }}</code></span>
            <span class="hxd-arrow">→</span>
            <span class="hxd-be"><span class="mono">{{ l.backend }}</span>
              <span class="hxd-dots" :title="backendsBy.get(l.backend) ? backendHealth(backendsBy.get(l.backend)!).label : 'missing backend'">
                <i v-for="(s, j) in dots(backendsBy.get(l.backend))" :key="j" :class="s" /><Badge v-if="!backendsBy.get(l.backend)" level="critical" label="missing" /></span></span>
            <span class="hxd-lane-rate dim">{{ fmtN(beRate(l.backend)) }}/s</span>
          </li>
          <li v-if="!shape(f).lanes.length" class="dim">No backend: requests get a 503.</li>
          <li v-if="!f.simple_routes" class="dim">Also has custom routing rules (see Raw config).</li>
        </ul>
      </article>
    </div>
  </section>

  <!-- traffic chart -->
  <section v-if="on('traffic')" class="card hxd-section">
    <div class="card-head hxd-chart-head"><h2>Traffic</h2>
      <div class="segmented sm" role="group" aria-label="Metric">
        <button v-for="(m, k) in METRICS" :key="k" :aria-pressed="d.metric === k" @click="d.metric = k">{{ m.label }}</button></div>
      <div class="segmented sm" role="group" aria-label="Compare">
        <button :aria-pressed="by === 'frontend'" :disabled="METRICS[d.metric]?.backendOnly" @click="d.by = 'frontend'">By frontend</button>
        <button :aria-pressed="by === 'backend'" @click="d.by = 'backend'">By backend</button></div>
      <span class="grow" />
      <div class="segmented sm" role="group" aria-label="Time range">
        <button :aria-pressed="d.range === '15m'" @click="d.range = '15m'">15 min</button><button :aria-pressed="d.range === '1h'" @click="d.range = '1h'">1 hour</button></div>
    </div>
    <div class="card-body">
      <div class="hxd-legend">
        <button v-for="n in names" :key="n" class="hxd-legend-item" :aria-pressed="!d.only.length || d.only.includes(n)" @click="toggleOnly(n)">
          <i :style="{ background: colorOf(n) }" />{{ n }}</button>
        <button v-if="d.only.length" class="link" @click="d.only = []">Show all</button></div>
      <LineChart :series="chart" :y-fmt="(METRICS[d.metric] || METRICS.req).fmt" :range="d.range === '15m' ? 15 * 60e3 : 60 * 60e3" :height="240"
        empty-sub="Collecting samples (every 10 s)…" :aria-label="`${(METRICS[d.metric] || METRICS.req).label} by ${by}`" />
    </div>
  </section>

  <!-- topology -->
  <section v-if="on('topology')" class="card hxd-section"><div class="card-head"><h2>Topology</h2><div class="right"><button class="btn sm" @click="emit('go', 'haproxy-topology')">Full view</button></div></div>
    <div class="card-body"><HxTopology :data="data" @pick="(k: string, n: string) => emit('pick', k, n)" /></div></section>
</template>

<style scoped>
.grow { flex: 1; }
.hxd-status { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; padding: 14px 16px; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); }
.hxd-status-ic { display: grid; place-items: center; width: 36px; height: 36px; border-radius: 50%; }
.hxd-status-ic .icon { width: 20px; height: 20px; }
.hxd-status.good .hxd-status-ic { background: color-mix(in srgb, var(--good) 16%, transparent); color: var(--good); }
.hxd-status.warning .hxd-status-ic { background: color-mix(in srgb, var(--warning) 16%, transparent); color: var(--warning); }
.hxd-status.critical { border-color: color-mix(in srgb, var(--critical) 45%, var(--border)); }
.hxd-status.critical .hxd-status-ic { background: color-mix(in srgb, var(--critical) 16%, transparent); color: var(--critical); }
.hxd-status-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.hxd-status-text b { font-size: 15px; }
.hxd-status-text .hint { overflow-wrap: anywhere; }

.hxd-custom { margin-top: 12px; }
.hxd-custom-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.hxd-custom-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 8px; }
.hxd-check { display: flex; gap: 10px; align-items: flex-start; padding: 10px 12px; border: 1px solid var(--border); border-radius: 10px; cursor: pointer; }
.hxd-check:hover { background: var(--surface-hover); }
.hxd-check input { margin-top: 3px; }
.hxd-check > span { display: flex; flex-direction: column; gap: 2px; }
.hxd-custom-foot { margin-top: 12px; }


.hxd-section { margin-top: 16px; }
.hxd-section .list li { align-items: center; }
.hxd-go { margin-left: auto; font-size: 12.5px; color: var(--accent-ink, var(--accent)); white-space: nowrap; }
.hxd-head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; margin: 4px 2px 10px; }
.hxd-head h2 { margin: 0; font-size: 15px; }

.hxd-fes { display: grid; grid-template-columns: repeat(auto-fill, minmax(420px, 1fr)); gap: 12px; }
@media (max-width: 700px) { .hxd-fes { grid-template-columns: 1fr; } }
.hxd-fe { padding: 0; overflow: hidden; }
.hxd-fe-head { display: flex; flex-direction: column; gap: 6px; padding: 14px 16px 12px; cursor: pointer; border-bottom: 1px solid var(--border); }
.hxd-fe-head:hover { background: var(--surface-hover); }
.hxd-fe-title { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.hxd-fe-title .mono { font-size: 15px; font-weight: 650; }
.hxd-fe-title .chip .icon { width: 12px; height: 12px; margin-right: 4px; }
.hxd-fe-listen { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; font-size: 12.5px; color: var(--text-2); }
.hxd-fe-listen .icon { width: 12px; height: 12px; margin-right: 3px; vertical-align: -1px; color: var(--good); }
.hxd-fe-nums { display: flex; gap: 16px; flex-wrap: wrap; font-size: 12.5px; color: var(--muted); }
.hxd-fe-nums b { color: var(--text); font-variant-numeric: tabular-nums; }
.hxd-fe-nums .warn b { color: var(--warning); } .hxd-fe-nums .bad b { color: var(--critical); }
.hxd-lanes { list-style: none; margin: 0; padding: 6px 0; }
.hxd-lanes li { display: grid; grid-template-columns: minmax(0, 1.1fr) 18px minmax(0, 1fr) 56px; align-items: center; gap: 8px; padding: 7px 16px; font-size: 13px; }
.hxd-lanes li.clickable { cursor: pointer; } .hxd-lanes li.clickable:hover { background: var(--surface-hover); }
.hxd-match { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hxd-arrow { color: var(--muted); text-align: center; }
.hxd-be { display: flex; align-items: center; gap: 8px; min-width: 0; }
.hxd-be .mono { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hxd-dots { display: inline-flex; gap: 3px; align-items: center; flex-wrap: wrap; }
.hxd-dots i { width: 8px; height: 8px; border-radius: 50%; display: block; }
.hxd-dots .up { background: var(--good); } .hxd-dots .out { background: var(--warning); } .hxd-dots .down { background: var(--critical); }
.hxd-lane-rate { text-align: right; font-variant-numeric: tabular-nums; font-size: 12px; }

.hxd-chart-head { flex-wrap: wrap; gap: 10px; }
.hxd-legend { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; align-items: center; }
.hxd-legend-item { display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px; border: 1px solid var(--border); border-radius: 999px; background: none; color: var(--text); font: inherit; font-size: 12.5px; cursor: pointer; }
.hxd-legend-item i { width: 8px; height: 8px; border-radius: 50%; }
.hxd-legend-item[aria-pressed="false"] { opacity: .45; }
.hxd-legend-item:hover { background: var(--surface-hover); }
</style>
