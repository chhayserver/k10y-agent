<script setup lang="ts">
// A row of number tiles with a trend line (or a health bar). Used by the HAProxy dashboard, Frontends and Backends.
export interface Kpi { k: string; label: string; value: string; of?: number; sub: string; spark?: number[]; bar?: { up: number; out: number; down: number }; level?: string }
defineProps<{ items: Kpi[]; clickable?: boolean }>();
const emit = defineEmits<{ pick: [k: string] }>();
function spark(values: number[] | undefined) {
  if (!values || values.length < 2) return "";
  const max = Math.max(...values, 1e-9), step = 100 / (values.length - 1);
  return values.map((v, i) => `${i ? "L" : "M"}${(i * step).toFixed(1)},${(30 - (v / max) * 26).toFixed(1)}`).join(" ");
}
</script>

<template>
  <div class="hxk">
    <component :is="clickable ? 'button' : 'div'" v-for="k in items" :key="k.k" class="card hxk-tile" :class="[k.level, { clickable }]" @click="clickable && emit('pick', k.k)">
      <span class="lbl">{{ k.label }}</span>
      <b>{{ k.value }}<small v-if="k.of != null"> / {{ k.of }}</small></b>
      <span v-if="k.bar" class="hxk-bar"><i class="up" :style="{ flexGrow: k.bar.up }" /><i class="out" :style="{ flexGrow: k.bar.out }" /><i class="down" :style="{ flexGrow: k.bar.down }" /></span>
      <svg v-else-if="spark(k.spark)" class="hxk-spark" viewBox="0 0 100 32" preserveAspectRatio="none" aria-hidden="true"><path :d="spark(k.spark)" /></svg>
      <span v-else class="hxk-spark" />
      <span class="hint">{{ k.sub }}</span>
    </component>
  </div>
</template>

<style scoped>
.hxk { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; }
.hxk-tile { display: flex; flex-direction: column; align-items: flex-start; gap: 4px; padding: 14px 16px; text-align: left; font: inherit; color: inherit; min-width: 0; }
.hxk-tile.clickable { cursor: pointer; } .hxk-tile.clickable:hover { border-color: var(--border-strong, var(--muted)); }
.hxk-tile .lbl { font-size: 12px; color: var(--muted); font-weight: 550; }
.hxk-tile b { font-size: 26px; font-weight: 650; font-variant-numeric: tabular-nums; line-height: 1.15; }
.hxk-tile b small { font-size: 15px; color: var(--muted); font-weight: 500; }
.hxk-tile.warning b { color: var(--warning); } .hxk-tile.critical b { color: var(--critical); }
.hxk-tile .hint { font-size: 12px; }
.hxk-spark { width: 100%; height: 30px; display: block; }
.hxk-spark path { fill: none; stroke: var(--accent); stroke-width: 1.6; vector-effect: non-scaling-stroke; }
.hxk-tile.critical .hxk-spark path { stroke: var(--critical); } .hxk-tile.warning .hxk-spark path { stroke: var(--warning); }
.hxk-bar { display: flex; width: 100%; height: 8px; border-radius: 4px; overflow: hidden; background: var(--border); margin: 11px 0; }
.hxk-bar i { display: block; min-width: 0; } .hxk-bar .up { background: var(--good); } .hxk-bar .out { background: var(--warning); } .hxk-bar .down { background: var(--critical); }
</style>
