<script setup lang="ts">
// Live charts for the last hour, from the hub's 10-second samples.
import { computed } from "vue";
import LineChart from "@/components/LineChart.vue";
import type { HxPoint } from "@/lib/haproxy";

const props = defineProps<{ points: HxPoint[]; services: string[]; backends: string[] }>();
const COLORS = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)", "var(--series-5)", "var(--series-6)", "var(--series-7)", "var(--series-8)"];
function series(kind: "f" | "b", names: string[], field: string) {
  return names.map((n, i) => ({ label: n, color: COLORS[i % COLORS.length],
    points: props.points.filter((p) => p[1][kind][n]).map((p) => [p[0], Number((p[1][kind][n] as any)[field]) || 0] as [number, number]) }));
}
const req = computed(() => series("f", props.services, "req"));
const sess = computed(() => series("f", props.services, "sess"));
const err = computed(() => series("f", props.services, "e5"));
const rtime = computed(() => series("b", props.backends, "rtime"));
const RANGE = 60 * 60e3;
const n = (v: number) => (v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2).replace(/\.?0+$/, "") || "0");
</script>
<template>
  <div class="hx-charts">
    <section class="card"><div class="card-head"><h2>Requests / s</h2><span class="hint">per service</span></div>
      <div class="card-body"><LineChart :series="req" :y-fmt="n" :range="RANGE" empty-sub="Collecting samples (every 10 s)…" aria-label="Requests per second per service" /></div></section>
    <section class="card"><div class="card-head"><h2>Server errors (5xx) / s</h2><span class="hint">per service</span></div>
      <div class="card-body"><LineChart :series="err" :y-fmt="n" :range="RANGE" empty-sub="Collecting samples…" aria-label="5xx per second per service" /></div></section>
    <section class="card"><div class="card-head"><h2>Sessions</h2><span class="hint">current, per service</span></div>
      <div class="card-body"><LineChart :series="sess" :y-fmt="n" :range="RANGE" empty-sub="Collecting samples…" aria-label="Sessions per service" /></div></section>
    <section class="card"><div class="card-head"><h2>Response time</h2><span class="hint">ms, average per backend</span></div>
      <div class="card-body"><LineChart :series="rtime" :y-fmt="(v) => `${n(v)} ms`" :range="RANGE" empty-sub="Collecting samples…" aria-label="Average response time per backend" /></div></section>
  </div>
</template>
