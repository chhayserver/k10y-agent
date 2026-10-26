<script setup lang="ts">
// Backends as one table: a row per backend (health, traffic, settings), expandable to its servers.
import { computed, reactive, ref, watch } from "vue";
import Badge from "@/components/Badge.vue";
import EmptyState from "@/components/EmptyState.vue";
import Icon from "@/components/Icon.vue";
import RowMenu from "@/components/RowMenu.vue";
import { fmt } from "@/lib/format";
import { backendHealth, fmtRate, num, STATE, type HxBackend, type HxPoint, type HxServer } from "@/lib/haproxy";
import type { MenuItem } from "@/stores/ui";
import HxKpis, { type Kpi } from "./HxKpis.vue";

const props = defineProps<{
  backends: HxBackend[]; points: HxPoint[]; focus: string; canConfigure: boolean; instanceId: number;
  backendMenu: (b: HxBackend) => MenuItem[]; serverMenu: (b: HxBackend, s: HxServer) => MenuItem[];
}>();
const emit = defineEmits<{ newBackend: []; addServer: [b: HxBackend]; sync: [b: HxBackend] }>();

// --- filters, sort, expansion (remembered per HAProxy in this browser) -------------------
const KEY = `k10y.hx.backends.${props.instanceId}`;
let saved: { q?: string; filter?: string; usedBy?: string; sort?: string; dir?: number; open?: string[] } = {};
try { saved = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch { /* storage blocked */ }
const q = ref(saved.q || ""), filter = ref(saved.filter || "all"), usedBy = ref(saved.usedBy || "");
const sort = reactive({ key: saved.sort || "health", dir: saved.dir || 1 });
const open = ref(new Set<string>(saved.open || []));
const touched = ref(!!saved.open);
watch([q, filter, usedBy, () => sort.key, () => sort.dir, open], () => {
  try { localStorage.setItem(KEY, JSON.stringify({ q: q.value, filter: filter.value, usedBy: usedBy.value, sort: sort.key, dir: sort.dir, open: [...open.value] })); } catch { /* */ }
}, { deep: true });

const problem = (b: HxBackend) => ["critical", "warning"].includes(backendHealth(b).level);
// First visit: open the backends that have problems, so issues are visible straight away.
watch(() => props.backends, (bs) => { if (!touched.value) open.value = new Set(bs.filter(problem).map((b) => b.name)); }, { immediate: true });
watch(() => props.focus, (f) => { if (f.startsWith("b:")) { open.value.add(f.slice(2)); open.value = new Set(open.value); } }, { immediate: true });
function toggle(name: string) {
  touched.value = true;
  const s = new Set(open.value); s.has(name) ? s.delete(name) : s.add(name); open.value = s;
}
function expandAll(on: boolean) { touched.value = true; open.value = new Set(on ? rows.value.map((r) => r.b.name) : []); }

const HEALTH_RANK: Record<string, number> = { critical: 0, warning: 1, neutral: 2, good: 3 };
const rows = computed(() => {
  const s = q.value.trim().toLowerCase();
  const list = props.backends.map((b) => {
    const h = backendHealth(b);
    const counts = { up: 0, out: 0, down: 0 };
    for (const sv of b.servers) {
      if (sv.state === "down") counts.down++; else if (sv.state === "drain" || sv.state === "maint") counts.out++; else counts.up++;
    }
    const weightTotal = b.servers.filter((x) => !x.backup && (x.state === "up" || x.state === "nocheck")).reduce((a, x) => a + (x.weight || 0), 0);
    return { b, h, counts, weightTotal, sessions: Number(b.stats.scur) || 0, rate: Number(b.stats.rate) || 0, e5: Number(b.stats.hrsp_5xx) || 0, rtime: Number(b.stats.rtime) || 0 };
  }).filter((r) => (!usedBy.value || (usedBy.value === "-" ? !r.b.used_by.length : r.b.used_by.includes(usedBy.value)))
    && (filter.value === "all" || (filter.value === "problems" && problem(r.b)) || (filter.value === "k8s" && r.b.k8s_sync))
    && (!s || r.b.name.toLowerCase().includes(s) || r.b.servers.some((x) => `${x.name} ${x.address}`.toLowerCase().includes(s))));
  const val = (r: (typeof list)[number]): string | number => ({ name: r.b.name, health: HEALTH_RANK[r.h.level] * 1000 + r.counts.up, sessions: r.sessions, rate: r.rate, e5: r.e5, rtime: r.rtime } as any)[sort.key];
  return list.sort((a, b) => { const x = val(a), y = val(b); return (typeof x === "number" ? (x as number) - (y as number) : String(x).localeCompare(String(y))) * sort.dir || a.b.name.localeCompare(b.b.name); });
});
const totals = computed(() => {
  const all = props.backends.flatMap((b) => b.servers);
  return { backends: props.backends.length, servers: all.length, down: all.filter((x) => x.state === "down").length,
    out: all.filter((x) => x.state === "drain" || x.state === "maint").length, problems: props.backends.filter(problem).length };
});
const frontendNames = computed(() => [...new Set(props.backends.flatMap((b) => b.used_by))].sort());
const filtered = computed(() => !!(q.value || filter.value !== "all" || usedBy.value));
function clear() { q.value = ""; filter.value = "all"; usedBy.value = ""; }
// Numbers for the backends shown, so the tiles follow the filters.
const kpis = computed<Kpi[]>(() => {
  const bs = rows.value.map((r) => r.b), names = bs.map((b) => b.name), pts = props.points.slice(-60).map((p) => p[1]), last = pts[pts.length - 1];
  const sumAt = (p: HxPoint[1], k: "req" | "e5" | "sess") => names.reduce((a, n) => a + (p.b[n]?.[k] || 0), 0);
  const rtAt = (p: HxPoint[1]) => { const v = names.map((n) => p.b[n]?.rtime || 0).filter((x) => x > 0); return v.length ? v.reduce((a, x) => a + x, 0) / v.length : 0; };
  const sv = bs.flatMap((b) => b.servers), up = sv.filter((x) => x.state === "up" || x.state === "nocheck").length;
  const down = sv.filter((x) => x.state === "down").length, out = sv.length - up - down;
  const req = last ? sumAt(last, "req") : 0, pct = req ? Math.min(100, (100 * sumAt(last!, "e5")) / req) : 0, rt = last ? rtAt(last) : 0;
  return [
    { k: "be", label: filtered.value ? "Backends shown" : "Backends", value: String(bs.length), of: filtered.value ? props.backends.length : undefined,
      sub: `${bs.filter(problem).length} with problems · ${bs.filter((b) => b.k8s_sync).length} from Kubernetes` },
    { k: "srv", label: "Servers up", value: String(up), of: sv.length, sub: down || out ? `${down} down · ${out} out of rotation` : "all in rotation",
      bar: { up, out, down }, level: down ? "critical" : out ? "warning" : "" },
    { k: "req", label: "New sessions / s", value: fmtRate(req), sub: "sent to these backends", spark: pts.map((p) => sumAt(p, "req")) },
    { k: "err", label: "Error rate", value: `${pct < 10 ? pct.toFixed(1) : pct.toFixed(0)}%`, sub: "5xx from servers", spark: pts.map((p) => { const r = sumAt(p, "req"); return r ? Math.min(100, (100 * sumAt(p, "e5")) / r) : 0; }), level: pct >= 5 ? "critical" : pct >= 1 ? "warning" : "" },
    { k: "rt", label: "Response time", value: rt ? `${Math.round(rt)} ms` : "–", sub: "average", spark: pts.map(rtAt) },
  ];
});
function setSort(key: string) { if (sort.key === key) sort.dir = -sort.dir; else { sort.key = key; sort.dir = key === "name" || key === "health" ? 1 : -1; } }
const share = (r: { weightTotal: number }, sv: HxServer) => (sv.backup || !(sv.state === "up" || sv.state === "nocheck") || !r.weightTotal ? null : Math.round((100 * (sv.weight || 0)) / r.weightTotal));
const checkText = (b: HxBackend) => (b.check.type === "http" ? `GET ${b.check.uri}` : b.check.type === "tcp" ? "TCP" : "connect");
const COLS: [string, string, boolean][] = [["name", "Backend", false], ["health", "Health", false], ["", "Settings", false], ["sessions", "Sessions", true], ["rate", "New/s", true], ["e5", "5xx", true], ["rtime", "Resp. time", true]];
</script>

<template>
  <HxKpis :items="kpis" />
  <div class="toolbar hxb-tools">
    <label class="search"><Icon name="search" /><input v-model="q" class="input" placeholder="Search backends, servers or addresses" aria-label="Search"></label>
    <div class="segmented" role="group" aria-label="Filter">
      <button :aria-pressed="filter === 'all'" @click="filter = 'all'">All</button>
      <button :aria-pressed="filter === 'problems'" @click="filter = 'problems'">Problems<span v-if="totals.problems" class="count-pill">{{ totals.problems }}</span></button>
      <button :aria-pressed="filter === 'k8s'" @click="filter = 'k8s'">From Kubernetes</button></div>
    <select v-model="usedBy" class="select hxb-used" aria-label="Used by frontend">
      <option value="">Used by any frontend</option><option v-for="n in frontendNames" :key="n" :value="n">Used by {{ n }}</option><option value="-">Not used by any frontend</option></select>
    <button v-if="filtered" class="link" @click="clear">Clear filters</button>
    <span class="grow" />
    <button v-if="canConfigure" class="btn primary" @click="emit('newBackend')"><Icon name="upload" /><span>New backend</span></button>
  </div>
  <div class="hxb-bar2"><button class="btn ghost sm" @click="expandAll(true)">Expand all</button><button class="btn ghost sm" @click="expandAll(false)">Collapse all</button>
    <span class="grow" /><span class="hint">Drain and Maintenance apply instantly, without a reload.</span></div>

  <section class="card flush"><div class="card-body table-wrap">
    <EmptyState v-if="!rows.length" :title="backends.length ? 'No backends match' : 'No backends yet'" icon="nodes"
      :sub="backends.length ? 'Change the search or the filter.' : 'Create a backend to group the servers that share traffic.'" />
    <table v-else class="data hxb">
      <thead><tr>
        <th class="hxb-caret"><span class="sr-only">Expand</span></th>
        <th v-for="[k, label, isNum] in COLS" :key="label" :class="{ num: isNum }" :aria-sort="k && sort.key === k ? (sort.dir === 1 ? 'ascending' : 'descending') : undefined">
          <button v-if="k" class="th-btn" @click="setSort(k)">{{ label }}<Icon name="sort" /></button><span v-else class="th-btn" style="cursor:default">{{ label }}</span></th>
        <th class="actions"><span class="sr-only">Actions</span></th></tr></thead>
      <tbody v-for="r in rows" :id="`b:${r.b.name}`" :key="r.b.name" :class="{ 'hx-focus': focus === `b:${r.b.name}`, open: open.has(r.b.name) }">
        <!-- backend row -->
        <tr class="hxb-row" :class="r.h.level" tabindex="0" :aria-expanded="open.has(r.b.name)" @click="(e) => !(e.target as HTMLElement).closest('button, a') && toggle(r.b.name)"
          @keydown.enter.self.prevent="toggle(r.b.name)" @keydown.space.self.prevent="toggle(r.b.name)">
          <td class="hxb-caret"><Icon name="sort" :class="{ turned: open.has(r.b.name) }" /></td>
          <td class="primary"><div class="hxb-name"><span class="mono">{{ r.b.name }}</span>
            <button v-if="r.b.k8s_sync" class="chip hx-sync" :class="{ err: r.b.k8s_sync.last_error }" :title="r.b.k8s_sync.last_error || r.b.k8s_sync.last_result" @click="emit('sync', r.b)">
              <Icon name="logo" /> {{ r.b.k8s_sync.namespace }}/{{ r.b.k8s_sync.service }}{{ r.b.k8s_sync.enabled ? "" : " (paused)" }}</button></div>
            <div class="sub">{{ r.b.used_by.length ? `used by ${r.b.used_by.join(", ")}` : "not used by any frontend" }}</div></td>
          <td><div class="hxb-health"><Badge :level="r.h.level" :label="r.h.label" />
            <span v-if="r.b.servers.length" class="hxb-bar" :title="`${r.counts.up} up · ${r.counts.out} out of rotation · ${r.counts.down} down`">
              <i class="up" :style="{ flexGrow: r.counts.up }" /><i class="out" :style="{ flexGrow: r.counts.out }" /><i class="down" :style="{ flexGrow: r.counts.down }" /></span></div></td>
          <td class="sub-cell">{{ r.b.balance }} · {{ r.b.mode.toUpperCase() }}<div class="sub">check {{ checkText(r.b) }}</div></td>
          <td class="num">{{ num(r.sessions) }}</td><td class="num">{{ num(r.rate) }}</td>
          <td class="num"><b v-if="r.e5" style="color:var(--critical)">{{ num(r.e5) }}</b><span v-else class="dim">0</span></td>
          <td class="num">{{ r.rtime ? `${r.rtime} ms` : "–" }}</td>
          <td class="actions"><RowMenu :items="() => backendMenu(r.b)" :label="r.b.name" /></td>
        </tr>
        <!-- servers -->
        <template v-if="open.has(r.b.name)">
          <tr class="hxb-srvhead"><td /><td colspan="8"><div class="hxb-srvgrid hdr">
            <span>Server</span><span>State</span><span>Address</span><span class="num">Weight</span><span>Health check</span><span class="num">Sessions</span><span class="num">Requests</span><span class="num">5xx</span><span>In state for</span><span /></div></td></tr>
          <tr v-if="!r.b.servers.length" class="hxb-srv"><td /><td colspan="8" class="dim">No servers yet.
            <button v-if="canConfigure" class="link" style="margin-left:8px" @click="emit('addServer', r.b)">Add a server</button></td></tr>
          <tr v-for="sv in r.b.servers" :key="sv.name" class="hxb-srv" :class="sv.state"><td /><td colspan="8"><div class="hxb-srvgrid">
            <span class="mono strong">{{ sv.name }}<span v-if="sv.backup" class="chip">backup</span></span>
            <span><Badge :level="STATE[sv.state].level" :label="STATE[sv.state].label" /></span>
            <span class="mono">{{ sv.address }}:{{ sv.port }}</span>
            <span class="num">{{ sv.weight }}<span v-if="share(r, sv) != null" class="dim"> · {{ share(r, sv) }}%</span></span>
            <span :title="sv.stats.check_desc || ''"><template v-if="!sv.check"><span class="dim">off</span></template>
              <template v-else><code :class="{ bad: sv.state === 'down' }">{{ (sv.stats.check_status || "").replace("* ", "") || "–" }}</code>
                <span v-if="sv.stats.check_duration != null" class="dim" style="margin-left:6px">{{ sv.stats.check_duration }} ms</span></template></span>
            <span class="num">{{ num(sv.stats.scur) }}</span><span class="num">{{ num(sv.stats.stot) }}</span>
            <span class="num"><b v-if="Number(sv.stats.hrsp_5xx)" style="color:var(--critical)">{{ num(sv.stats.hrsp_5xx) }}</b><span v-else class="dim">0</span></span>
            <span class="dim">{{ sv.stats.lastchg != null ? fmt.age(Number(sv.stats.lastchg)) : "–" }}</span>
            <span class="hxb-act"><RowMenu :items="() => serverMenu(r.b, sv)" :label="sv.name" /></span>
          </div></td></tr>
          <tr v-if="canConfigure && r.b.servers.length" class="hxb-srv add"><td /><td colspan="8"><button class="link" @click="emit('addServer', r.b)">+ Add server to {{ r.b.name }}</button></td></tr>
        </template>
      </tbody>
    </table>
  </div></section>
</template>

<style scoped>
.hxb-tools { margin-top: 16px; }
.hxb-bar2 { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin: -4px 0 12px; }
.hxb-bar2 .grow { flex: 1; }
.hxb-used { width: auto; max-width: 230px; }
table.hxb tbody + tbody { border-top: 1px solid var(--border); }
.hxb-caret { width: 30px; padding-right: 0 !important; }
.hxb-caret .icon { width: 14px; height: 14px; color: var(--muted); transform: rotate(90deg); transition: transform .15s; }
.hxb-caret .icon.turned { transform: rotate(180deg); color: var(--text); }
.hxb-row { cursor: pointer; }
.hxb-row:hover td, .hxb-row:focus-visible td { background: var(--surface-hover); }
.hxb-row:focus-visible { outline: none; }
.hxb-row td:first-child { box-shadow: inset 3px 0 0 transparent; }
.hxb-row.critical td:first-child { box-shadow: inset 3px 0 0 var(--critical); }
.hxb-row.warning td:first-child { box-shadow: inset 3px 0 0 #e8a33a; }
.hxb-name { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.sub { font-size: 12px; color: var(--muted); font-weight: 400; margin-top: 2px; }
.sub-cell { font-size: 13px; }
.hxb-health { display: flex; align-items: center; gap: 10px; }
.hxb-bar { display: flex; width: 70px; height: 6px; border-radius: 3px; overflow: hidden; background: var(--border); }
.hxb-bar i { display: block; min-width: 0; } .hxb-bar i.up { background: var(--good); } .hxb-bar i.out { background: #e8a33a; } .hxb-bar i.down { background: var(--critical); }
tbody.open .hxb-row td { background: var(--surface-active); }
.hxb-srvhead td, .hxb-srv td { padding-top: 0 !important; padding-bottom: 0 !important; background: color-mix(in srgb, var(--surface-active) 45%, transparent); }
.hxb-srvgrid { display: grid; grid-template-columns: minmax(110px, 1.2fr) 120px minmax(120px, 1.2fr) 90px minmax(110px, 1fr) 70px 80px 50px 90px 36px;
  gap: 10px; align-items: center; padding: 8px 0 8px 14px; border-left: 2px solid var(--border); font-size: 13px; }
.hxb-srvgrid.hdr { padding-top: 10px; padding-bottom: 6px; font-size: 11.5px; font-weight: 600; letter-spacing: .04em; text-transform: uppercase; color: var(--muted); }
.hxb-srvgrid .num { text-align: right; font-variant-numeric: tabular-nums; }
.hxb-srvgrid .strong { font-weight: 600; }
.hxb-srvgrid code.bad { color: var(--critical); }
.hxb-srv:hover td { background: var(--surface-hover); }
.hxb-srv.down .hxb-srvgrid { border-left-color: var(--critical); }
.hxb-srv.drain .hxb-srvgrid, .hxb-srv.maint .hxb-srvgrid { border-left-color: #e8a33a; }
.hxb-srv.add .hxb-srvgrid, .hxb-srv.add td { padding: 8px 0 10px 14px; }
.hxb-act { display: flex; justify-content: flex-end; }
@media (max-width: 1279px) {
  table.hxb tr > :nth-child(4), table.hxb tr > :nth-child(6), table.hxb tr > :nth-child(8) { display: none; }
  .hxb-summary .hint { flex-basis: 100%; } .hxb-srvgrid { grid-template-columns: minmax(90px, 1fr) 90px minmax(100px, 1fr) 70px minmax(80px, 1fr) 32px; }
  .hxb-srvgrid > :nth-child(n+6):not(:last-child) { display: none; } }
</style>
