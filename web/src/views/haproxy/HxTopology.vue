<script setup lang="ts">
// Services (frontends) → backends → servers, with live health colours. Hover a box to trace its path.
import { computed, ref } from "vue";
import { backendHealth, bindText, ROUTE_KIND, routeText, STATE, type HxOverview } from "@/lib/haproxy";

const props = defineProps<{ data: HxOverview }>();
const emit = defineEmits<{ pick: [kind: "frontend" | "backend" | "server", name: string, backend?: string] }>();
const W = [250, 230, 250], GAP = 120, NODE_H = 54, SRV_H = 40, PAD = 14;
const X = [0, W[0] + GAP, W[0] + GAP + W[1] + GAP];
const hover = ref<string | null>(null);

const layout = computed(() => {
  const fes = props.data.frontends.filter((f) => !f.stats_page);
  const bes = props.data.backends;
  // order backends by first frontend that uses them, so lines cross less
  const order = new Map<string, number>();
  fes.forEach((f, i) => [f.default_backend, ...f.routes.map((r) => r.backend)].forEach((b, j) => b && !order.has(b) && order.set(b, i * 100 + j)));
  const sortedBes = [...bes].sort((a, b) => (order.get(a.name) ?? 1e6) - (order.get(b.name) ?? 1e6));
  const servers: { key: string; be: string; s: any; y: number }[] = [];
  let ys = 0;
  const beNodes = sortedBes.map((b) => {
    const top = ys;
    b.servers.forEach((s) => { servers.push({ key: `s:${b.name}/${s.name}`, be: b.name, s, y: ys }); ys += SRV_H + 8; });
    if (!b.servers.length) ys += SRV_H + 8;
    ys += PAD;
    const mid = (top + ys - PAD - 8) / 2 - NODE_H / 2 + SRV_H / 2;
    return { key: `b:${b.name}`, b, y: Math.max(mid, 0), h: backendHealth(b) };
  });
  // spread backends so they never overlap
  for (let i = 1; i < beNodes.length; i++) beNodes[i].y = Math.max(beNodes[i].y, beNodes[i - 1].y + NODE_H + 14);
  const step = Math.max(NODE_H + 22, (Math.max(ys, beNodes.length * (NODE_H + 14)) ) / Math.max(fes.length, 1));
  const feNodes = fes.map((f, i) => ({ key: `f:${f.name}`, f, y: i * step }));
  const beY = new Map(beNodes.map((n) => [n.b.name, n.y]));
  const edges: { id: string; from: string; to: string; d: string; label: string; cls: string; ly: number }[] = [];
  const curve = (x1: number, y1: number, x2: number, y2: number) => `M${x1},${y1} C${(x1 + x2) / 2},${y1} ${(x1 + x2) / 2},${y2} ${x2},${y2}`;
  for (const n of feNodes) {
    const raw: [string, string][] = n.f.routes.map((r) => [r.backend, r.kind === "other" ? r.value : `${ROUTE_KIND[r.kind].toLowerCase()} ${routeText(r)}`]);
    if (n.f.default_backend) raw.push([n.f.default_backend, "default"]);
    // One line per service -> backend pair; several routes to the same backend share it ("2 routes").
    const byBackend = new Map<string, string[]>();
    raw.forEach(([be, label]) => byBackend.set(be, [...(byBackend.get(be) || []), label]));
    const targets = [...byBackend].map(([be, labels]) => [be, labels.length > 1 ? `${labels.length} routes` : labels[0]] as [string, string]);
    targets.forEach(([be, label], k) => {
      const y2 = beY.get(be);
      if (y2 == null) return;
      edges.push({ id: `${n.key}>${be}>${k}`, from: n.key, to: `b:${be}`, label, cls: "fe", ly: (n.y + y2) / 2 + NODE_H / 2 - 5,
        d: curve(X[0] + W[0], n.y + NODE_H / 2, X[1], y2 + NODE_H / 2) });
    });
  }
  for (const s of servers) {
    const y1 = (beY.get(s.be) ?? 0) + NODE_H / 2;
    edges.push({ id: `${s.key}`, from: `b:${s.be}`, to: s.key, label: "", cls: `srv ${s.s.state}`, ly: 0, d: curve(X[1] + W[1], y1, X[2], s.y + SRV_H / 2) });
  }
  const height = Math.max(ys, ...feNodes.map((n) => n.y + NODE_H), ...beNodes.map((n) => n.y + NODE_H)) + 10;
  return { feNodes, beNodes, servers, edges, height };
});

// Everything connected to the hovered box lights up.
const lit = computed(() => {
  const h = hover.value;
  if (!h) return null;
  const on = new Set([h]);
  for (let pass = 0; pass < 2; pass++)
    for (const e of layout.value.edges) {
      if (on.has(e.from) && (h.startsWith("f:") || h.startsWith("b:") || pass)) on.add(e.to);
      if (on.has(e.to) && (h.startsWith("s:") || h.startsWith("b:"))) on.add(e.from);
    }
  return on;
});
const dim = (k: string) => lit.value && !lit.value.has(k);
const edgeDim = (e: { from: string; to: string }) => lit.value && !(lit.value.has(e.from) && lit.value.has(e.to));
</script>

<template>
  <div class="hx-topo-wrap">
    <div class="hx-topo-legend"><span><i class="lg up" />Up</span><span><i class="lg drain" />Draining</span><span><i class="lg maint" />Maintenance</span><span><i class="lg down" />Down</span>
      <span class="hint">Hover a box to trace its traffic. Click to open it.</span></div>
    <div class="hx-topo-scroll">
      <svg :width="X[2] + W[2] + 4" :height="layout.height + 30" role="img" aria-label="HAProxy topology: services to backends to servers">
        <g transform="translate(2,26)">
          <text :x="X[0]" y="-10" class="col">SERVICES</text><text :x="X[1]" y="-10" class="col">BACKENDS</text><text :x="X[2]" y="-10" class="col">SERVERS</text>
          <g v-for="e in layout.edges" :key="e.id" :class="['edge', e.cls, { dim: edgeDim(e) }]">
            <path :d="e.d" />
          </g>
          <g v-for="e in layout.edges.filter((x) => x.label)" :key="e.id + 'l'" :class="{ dim: edgeDim(e) }">
            <text :x="X[0] + W[0] + GAP / 2" class="elabel" text-anchor="middle"
:y="e.ly">{{ e.label.length > 20 ? e.label.slice(0, 19) + "…" : e.label }}</text>
          </g>
          <g v-for="n in layout.feNodes" :key="n.key" :class="['node', 'fe', { dim: dim(n.key) }]" :transform="`translate(${X[0]},${n.y})`"
            tabindex="0" @mouseenter="hover = n.key" @mouseleave="hover = null" @focus="hover = n.key" @blur="hover = null" @click="emit('pick', 'frontend', n.f.name)">
            <rect :width="W[0]" :height="NODE_H" rx="10" />
            <text x="14" y="22" class="t">{{ n.f.name }}</text>
            <text x="14" y="40" class="s">{{ n.f.binds.map(bindText).join(", ").slice(0, 34) }} · {{ n.f.mode.toUpperCase() }}</text>
            <text :x="W[0] - 12" y="22" class="r" text-anchor="end">{{ n.f.stats.req_rate ?? n.f.stats.rate ?? 0 }}/s</text>
          </g>
          <g v-for="n in layout.beNodes" :key="n.key" :class="['node', 'be', n.h.level, { dim: dim(n.key) }]" :transform="`translate(${X[1]},${n.y})`"
            tabindex="0" @mouseenter="hover = n.key" @mouseleave="hover = null" @focus="hover = n.key" @blur="hover = null" @click="emit('pick', 'backend', n.b.name)">
            <rect :width="W[1]" :height="NODE_H" rx="10" /><rect class="bar" :width="5" :height="NODE_H" rx="2" />
            <text x="16" y="22" class="t">{{ n.b.name }}</text>
            <text x="16" y="40" class="s">{{ n.b.balance }} · {{ n.h.label }}</text>
          </g>
          <g v-for="s in layout.servers" :key="s.key" :class="['node', 'srv', s.s.state, { dim: dim(s.key) }]" :transform="`translate(${X[2]},${s.y})`"
            tabindex="0" @mouseenter="hover = s.key" @mouseleave="hover = null" @focus="hover = s.key" @blur="hover = null" @click="emit('pick', 'server', s.s.name, s.be)">
            <rect :width="W[2]" :height="SRV_H" rx="8" /><circle cx="16" :cy="SRV_H / 2" r="5" class="dot" />
            <text x="30" y="17" class="t sm">{{ s.s.name }}<tspan v-if="s.s.backup" class="s"> · backup</tspan></text>
            <text x="30" y="32" class="s">{{ s.s.address }}:{{ s.s.port }} · {{ STATE[s.s.state]?.label }}</text>
            <text :x="W[2] - 10" y="17" class="r" text-anchor="end">w {{ s.s.weight }}</text>
          </g>
        </g>
      </svg>
    </div>
  </div>
</template>
