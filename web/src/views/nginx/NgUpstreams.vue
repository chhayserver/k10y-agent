<script setup lang="ts">
// Upstreams: balancing, which sites use them, and every server with its check result and live share of traffic.
import { computed, nextTick, onMounted, reactive } from "vue";
import Badge from "@/components/Badge.vue";
import Icon from "@/components/Icon.vue";
import { fmt } from "@/lib/format";
import { rate, serverKey, serverState, siteName, upstreamHealth } from "@/lib/nginx";
import { useNginx } from "@/stores/nginx";

const props = defineProps<{ focus?: string }>();
const n = useNginx();
const f = reactive({ q: "", only: "all" });
const d = computed(() => n.data!);
const last = computed(() => n.last);
const list = computed(() => d.value.config.upstreams.filter((u) => {
  const q = f.q.trim().toLowerCase();
  const text = `${u.name} ${u.servers.map((s) => s.address).join(" ")}`.toLowerCase();
  return (f.only === "all" || ["critical", "warning"].includes(upstreamHealth(u, d.value.health).level)) && (!q || text.includes(q));
}));
const usedBy = (ids: string[]) => ids.map((id) => d.value.config.sites.find((s) => s.id === id)).filter(Boolean).map((s) => siteName(s!)).join(", ");
const share = (u: string, addr: string) => {
  const total = last.value?.upstreams?.[u]?.req || 0;
  const mine = last.value?.servers?.[`${u}/${addr}`]?.req || 0;
  return total ? Math.round((100 * mine) / total) : 0;
};
const METHOD: Record<string, string> = { round_robin: "Round robin", least_conn: "Least connections", ip_hash: "Client IP", hash: "Hash", random: "Random", least_time: "Least time" };
onMounted(async () => {
  if (props.focus) { await nextTick(); document.getElementById(`up-${props.focus}`)?.scrollIntoView({ block: "center" }); }
});
</script>

<template>
  <div class="toolbar">
    <label class="search"><Icon name="search" /><input v-model="f.q" class="input" placeholder="Search upstreams or servers" aria-label="Search upstreams"></label>
    <div class="segmented" role="group"><button type="button" :aria-pressed="f.only === 'all'" @click="f.only = 'all'">All</button>
      <button type="button" :aria-pressed="f.only === 'problems'" @click="f.only = 'problems'">Problems</button></div>
    <span class="result-count">{{ list.length }} of {{ d.config.upstreams.length }}</span>
  </div>
  <p v-if="!d.config.upstreams.length" class="hint">This Nginx has no upstream blocks. Sites that proxy straight to an address show it on the Sites page.</p>
  <section v-for="u in list" :id="`up-${u.name}`" :key="u.id" class="card ng-up" :class="{ focus: focus === u.name }">
    <div class="card-head ng-up-head">
      <div><h2 class="mono">{{ u.name }} <span v-if="u.context === 'stream'" class="pill-tag">TCP</span></h2>
        <div class="dim" style="font-size:12.5px;margin-top:4px">{{ METHOD[u.method] || u.method }}{{ u.method_args ? ` (${u.method_args})` : "" }}
          <template v-if="u.keepalive"> · keepalive {{ u.keepalive }}</template><template v-if="u.used_by.length"> · used by {{ usedBy(u.used_by) }}</template></div></div>
      <Badge :level="upstreamHealth(u, d.health).level" :label="upstreamHealth(u, d.health).label" lg />
      <div class="ng-nums"><span><b>{{ rate(last?.upstreams?.[u.name]?.req ?? 0) }}</b> req/s</span>
        <span :class="{ bad: (last?.upstreams?.[u.name]?.e5_pct ?? 0) >= 5 }"><b>{{ (last?.upstreams?.[u.name]?.e5_pct ?? 0).toFixed(1) }}%</b> 5xx</span></div>
    </div>
    <div class="card-body table-wrap" style="padding-top:0"><table class="data">
      <thead><tr><th>Server</th><th>State</th><th class="num">Weight</th><th>Check</th><th class="num">Share</th><th class="num">Req/s</th><th class="num">5xx</th><th class="num">p95</th></tr></thead>
      <tbody><tr v-for="s in u.servers" :key="s.id">
        <td class="mono">{{ s.address }} <span v-if="s.backup" class="pill-tag">backup</span></td>
        <td><Badge :level="serverState(s, d.health).level" :label="serverState(s, d.health).label" /></td>
        <td class="num">{{ s.weight }}</td>
        <td class="dim" :title="d.health[serverKey(s)]?.error || ''">{{ d.health[serverKey(s)] ? (d.health[serverKey(s)].up ? `${d.health[serverKey(s)].ms} ms` : d.health[serverKey(s)].error.slice(0, 48)) : "–" }}
          <span v-if="d.health[serverKey(s)]?.since" class="dim"> · changed {{ fmt.ago(new Date(d.health[serverKey(s)].since * 1000).toISOString()) }}</span></td>
        <td class="num"><span class="ng-share"><i :style="{ width: `${share(u.name, s.address)}%` }" /></span> {{ share(u.name, s.address) }}%</td>
        <td class="num">{{ rate(last?.servers?.[`${u.name}/${s.address}`]?.req ?? 0) }}</td>
        <td class="num" :class="{ bad: (last?.servers?.[`${u.name}/${s.address}`]?.e5_pct ?? 0) >= 5 }">{{ (last?.servers?.[`${u.name}/${s.address}`]?.e5_pct ?? 0).toFixed(1) }}%</td>
        <td class="num">{{ Math.round(last?.servers?.[`${u.name}/${s.address}`]?.p95 ?? 0) }} ms</td></tr></tbody></table>
      <p class="hint" style="margin:8px 0 0">The check is k10y's own TCP connect every 10 seconds; Nginx itself marks a server failed only after real requests fail (<code>max_fails</code>).</p>
    </div>
  </section>
</template>

<style scoped>
.ng-up { margin-bottom: 14px; }
.ng-up.focus { outline: 2px solid var(--accent); }
.ng-up-head { align-items: flex-start; gap: 16px; }
.ng-nums { margin-left: auto; display: flex; gap: 16px; font-size: 13px; color: var(--muted); white-space: nowrap; }
.ng-nums b { color: var(--text); font-weight: 650; }
.bad, .bad b { color: var(--critical) !important; }
.ng-share { display: inline-block; width: 60px; height: 6px; border-radius: 3px; background: var(--surface-active); vertical-align: middle; overflow: hidden; }
.ng-share i { display: block; height: 100%; background: var(--accent); }
</style>
