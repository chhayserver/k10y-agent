<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { fmt } from "@/lib/format";
import EmptyState from "./EmptyState.vue";

export interface Series { label: string; color: string; points: [number, number][] }
const props = withDefaults(defineProps<{ series: Series[]; yFmt: (v: number) => string; range: number; refs?: { v: number; label: string }[];
  area?: boolean; emptyTitle?: string; emptySub?: string; height?: number; ariaLabel?: string }>(), { refs: () => [], height: 220 });

const el = ref<HTMLElement | null>(null);
const width = ref(600);
const now = ref(Date.now());
let ro: ResizeObserver | null = null, timer = 0;
onMounted(() => {
  ro = new ResizeObserver(() => { if (el.value) width.value = Math.max(el.value.clientWidth, 260); });
  if (el.value) ro.observe(el.value);
  timer = window.setInterval(() => { now.value = Date.now(); }, 5000);
});
onBeforeUnmount(() => { ro?.disconnect(); clearInterval(timer); });

function niceStep(max: number) { const raw = max / 4, mag = 10 ** Math.floor(Math.log10(raw || 1)); return [1, 2, 2.5, 5, 10].map((f) => f * mag).find((s) => s >= raw) || mag * 10; }

const g = computed(() => {
  const W = width.value, H = props.height, t1 = now.value, t0 = t1 - props.range, multi = props.series.length > 1;
  const m = { t: 16, r: multi ? 112 : 76, b: 24, l: 56 }, iw = W - m.l - m.r, ih = H - m.t - m.b;
  const vis = props.series.map((s) => ({ ...s, pts: s.points.filter((p) => p[0] >= t0 - 30000) }));
  const max = Math.max(0, ...vis.flatMap((s) => s.pts.map((p) => p[1])), ...props.refs.map((r) => r.v));
  const step = niceStep(max || 1), top = step * Math.max(1, Math.ceil((max || 1) / step));
  const x = (t: number) => m.l + ((t - t0) / props.range) * iw;
  const y = (v: number) => m.t + ih - (Math.max(0, v) / top) * ih;
  const grid = [] as { y: number; label: string; base: boolean }[];
  for (let v = 0; v <= top + 1e-9; v += step) grid.push({ y: y(v), label: props.yFmt(v), base: v === 0 });
  const every = [60e3, 120e3, 300e3, 600e3, 900e3, 1800e3, 3600e3].find((ms) => (ms / props.range) * iw >= 72) || 3600e3;
  const ticks = [] as { x: number; label: string }[];
  for (let t = Math.ceil(t0 / every) * every; t <= t1; t += every) if (x(t) - m.l >= 24 && m.l + iw - x(t) >= 24) ticks.push({ x: x(t), label: fmt.time(t) });
  const path = (pts: [number, number][]) => pts.map((p, i) => `${i && p[0] - pts[i - 1][0] < 90000 ? "L" : "M"}${x(Math.max(p[0], t0)).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");
  const lines = vis.filter((s) => s.pts.length).map((s) => {
    const d = path(s.pts), last = s.pts[s.pts.length - 1];
    return { color: s.color, label: s.label, d, area: props.area && !multi ? `${d} L${x(last[0])},${y(0)} L${x(Math.max(s.pts[0][0], t0))},${y(0)} Z` : "",
      end: { cx: x(last[0]), cy: y(last[1]), ly: y(last[1]), text: props.yFmt(last[1]) } };
  });
  lines.sort((a, b) => a.end.ly - b.end.ly);
  for (let i = 1; i < lines.length; i++) if (lines[i].end.ly - lines[i - 1].end.ly < 15) lines[i].end.ly = lines[i - 1].end.ly + 15;
  return { W, H, m, iw, ih, t0, x, y, grid, ticks, lines, vis, multi, refs: props.refs.map((r) => ({ y: y(r.v), label: `${r.label} ${props.yFmt(r.v)}` })) };
});
const empty = computed(() => !g.value.vis.some((s) => s.pts.length));

const hover = ref<{ x: number; at: number; rows: { label: string; color: string; v: string; cy: number | null }[] } | null>(null);
function move(e: PointerEvent) {
  const svg = e.currentTarget as SVGElement, rect = svg.getBoundingClientRect(), G = g.value;
  const t = G.t0 + ((((e.clientX - rect.left) / rect.width) * G.W - G.m.l) / G.iw) * props.range;
  const ref0 = G.vis.find((s) => s.pts.length);
  if (!ref0) return;
  const near = (pts: [number, number][], tt: number) => pts.reduce((b, p) => (Math.abs(p[0] - tt) < Math.abs(b[0] - tt) ? p : b), pts[0]);
  const at = near(ref0.pts, t)[0];
  hover.value = { x: G.x(at), at, rows: G.vis.map((s) => {
    const p = s.pts.length ? near(s.pts, at) : null, ok = p && Math.abs(p[0] - at) < 30000;
    return { label: s.label, color: s.color, v: ok ? props.yFmt(p![1]) : "–", cy: ok ? G.y(p![1]) : null };
  }) };
}
</script>

<template>
  <div ref="el" class="chart" :style="{ height: `${height}px` }">
    <EmptyState v-if="empty" :title="emptyTitle || 'Waiting for data'" :sub="emptySub" icon="heart" />
    <template v-else>
      <svg :viewBox="`0 0 ${g.W} ${g.H}`" role="img" :aria-label="ariaLabel" @pointermove="move" @pointerdown="move" @pointerleave="hover = null">
        <template v-for="l in g.grid" :key="l.y">
          <line :x1="g.m.l" :x2="g.m.l + g.iw" :y1="l.y" :y2="l.y" :stroke="l.base ? 'var(--axis)' : 'var(--grid)'" />
          <text class="tick" :x="g.m.l - 8" :y="l.y + 4" text-anchor="end">{{ l.label }}</text>
        </template>
        <text v-for="t in g.ticks" :key="t.x" class="tick" :x="t.x" :y="g.H - 6" text-anchor="middle">{{ t.label }}</text>
        <template v-for="r in g.refs" :key="r.label">
          <line class="ref" :x1="g.m.l" :x2="g.m.l + g.iw" :y1="r.y" :y2="r.y" /><text class="ref-label" :x="g.m.l + 6" :y="r.y - 5">{{ r.label }}</text>
        </template>
        <template v-for="l in g.lines" :key="l.label">
          <path v-if="l.area" :d="l.area" :fill="l.color" opacity="0.1" />
          <path :d="l.d" fill="none" :stroke="l.color" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />
          <circle :cx="l.end.cx" :cy="l.end.cy" r="4" :fill="l.color" stroke="var(--surface)" stroke-width="2" />
          <text class="end-label" :x="l.end.cx + 9" :y="l.end.ly + 4">{{ l.end.text }}<tspan v-if="g.multi" class="dim"> {{ l.label }}</tspan></text>
        </template>
        <g v-if="hover">
          <line :x1="hover.x" :x2="hover.x" :y1="g.m.t" :y2="g.m.t + g.ih" stroke="var(--axis)" />
          <template v-for="r in hover.rows" :key="r.label"><circle v-if="r.cy != null" :cx="hover.x" :cy="r.cy" r="4" :fill="r.color" stroke="var(--surface)" stroke-width="2" /></template>
        </g>
        <rect :x="g.m.l" :y="g.m.t" :width="g.iw" :height="g.ih" fill="transparent" />
      </svg>
      <div v-if="hover" class="tooltip show" :style="{ left: `${(hover.x / g.W) * width + 184 > width ? (hover.x / g.W) * width - 184 : (hover.x / g.W) * width + 14}px`, top: `${g.m.t + 4}px` }">
        <div class="t">{{ fmt.timeSec(hover.at) }}</div>
        <div v-for="r in hover.rows" :key="r.label" class="row"><span class="swatch" :style="{ background: r.color }" />{{ r.label }}<b>{{ r.v }}</b></div>
      </div>
    </template>
  </div>
</template>
