<script setup lang="ts">
// A namespace's ResourceQuotas (used vs. hard, as bars) and LimitRanges (defaults and limits for containers).
import { onMounted, ref } from "vue";
import { agent } from "@/api/client";
import Icon from "@/components/Icon.vue";
import { useSession } from "@/stores/session";

const props = defineProps<{ namespace: string }>();
const s = useSession();
const d = ref<any>(null), error = ref("");
onMounted(async () => { try { d.value = await agent(s.current!.id).get("quotas", { namespace: props.namespace }); } catch (e: any) { error.value = e.message; } });
const level = (pct: number | null) => (pct == null ? "" : pct >= 90 ? "bad" : pct >= 75 ? "warn" : "");
const fmtMap = (m: Record<string, string>) => Object.entries(m).map(([k, v]) => `${k} ${v}`).join(", ") || "–";
</script>

<template>
  <div v-if="error || d?.quotas.length || d?.limit_ranges.length || d?.error">
    <h3 class="section-title">Quotas and limits</h3>
    <div v-if="error || d?.error" class="test-result show err"><Icon name="crit" /><span>{{ error || d.error }}</span></div>
    <div v-for="q in d?.quotas || []" :key="q.name" class="nq">
      <div class="nq-name">ResourceQuota <span class="mono">{{ q.name }}</span></div>
      <div v-for="i in q.items" :key="i.resource" class="nq-row">
        <span class="mono nq-res">{{ i.resource }}</span>
        <span class="nq-bar" :class="level(i.percent)"><i :style="{ width: `${Math.min(100, i.percent || 0)}%` }" /></span>
        <span class="nq-num">{{ i.used }} / {{ i.hard }}<span v-if="i.percent != null" class="dim"> · {{ i.percent }}%</span></span></div>
    </div>
    <div v-if="d?.limit_ranges.length" class="table-wrap card" style="box-shadow:none;margin-top:10px"><table class="data"><thead><tr><th>LimitRange</th><th>Type</th><th>Default limit</th><th>Default request</th><th>Max</th></tr></thead>
      <tbody><tr v-for="(l, i) in d.limit_ranges" :key="i"><td class="mono">{{ l.name }}</td><td>{{ l.type }}</td><td class="rp-wrap">{{ fmtMap(l.default) }}</td>
        <td class="rp-wrap">{{ fmtMap(l.default_request) }}</td><td class="rp-wrap">{{ fmtMap(l.max) }}</td></tr></tbody></table></div>
  </div>
</template>

<style scoped>
.nq { margin-bottom: 12px; }
.nq-name { font-size: 13px; font-weight: 600; margin-bottom: 6px; }
.nq-row { display: grid; grid-template-columns: minmax(120px, 200px) 1fr auto; gap: 10px; align-items: center; padding: 4px 0; font-size: 13px; }
.nq-bar { height: 8px; border-radius: 4px; background: var(--border); overflow: hidden; }
.nq-bar i { display: block; height: 100%; background: var(--good); }
.nq-bar.warn i { background: var(--warning); } .nq-bar.bad i { background: var(--critical); }
.nq-num { font-variant-numeric: tabular-nums; white-space: nowrap; }
</style>
