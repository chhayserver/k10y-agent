<script setup lang="ts">
// Rate limits and blocks: the IP blocklist, and live stick tables (who is tracked for a rate limit, with an
// Unblock button that resets a client's counters so it can send requests again).
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import EmptyState from "@/components/EmptyState.vue";
import Icon from "@/components/Icon.vue";
import { num } from "@/lib/haproxy";
import { hx } from "@/lib/haproxy";
import { openAction } from "@/stores/ui";
import HxBlocklist from "./HxBlocklist.vue";

const props = defineProps<{ instanceId: number; canOperate: boolean; canConfigure?: boolean }>();
const tables = ref<any[] | null>(null), table = ref(""), entries = ref<any[] | null>(null), error = ref(""), q = ref("");
async function loadTables() {
  try { tables.value = await hx.stickTables(props.instanceId); error.value = ""; if (!table.value && tables.value.length) table.value = tables.value[0].name; }
  catch (e: any) { error.value = e.message; }
}
async function loadEntries() {
  if (!table.value) return;
  try { entries.value = await hx.stickEntries(props.instanceId, table.value); } catch (e: any) { error.value = e.message; }
}
watch(table, () => { entries.value = null; loadEntries(); });
let timer = 0;
onMounted(async () => { await loadTables(); loadEntries(); timer = window.setInterval(() => { if (!document.hidden) loadEntries(); }, 5000); });
onBeforeUnmount(() => clearInterval(timer));
const current = computed(() => tables.value?.find((t) => t.name === table.value));
const COLS = ["http_req_rate", "http_req_cnt", "conn_cur", "conn_rate", "http_err_rate", "bytes_in_rate", "gpc0"];
const cols = computed(() => COLS.filter((c) => (entries.value || []).some((e) => e[c] != null)));
const limited = (e: any) => Object.keys(e).some((k) => k.endsWith("_rate") && Number(e[k]) > 0);
function unblock(e: any) {
  openAction({ title: `Let ${e.key} in again`, confirmLabel: "Reset its counters", noDryRun: true,
    desc: `Sets this client's request rates in table ${table.value} back to 0, so a rate limit stops refusing it right away. If it keeps sending too fast, the limit applies again.`,
    run: async () => { const r = await hx.stickReset(props.instanceId, table.value, e.key); await loadEntries(); return r.message; } });
}
const rows = computed(() => (entries.value || []).filter((e) => !q.value || String(e.key).includes(q.value.trim()))
  .sort((a, b) => (b.http_req_rate ?? b.conn_cur ?? 0) - (a.http_req_rate ?? a.conn_cur ?? 0)));
</script>
<template>
  <HxBlocklist :instance-id="instanceId" :can-operate="canOperate" :can-configure="canConfigure" />
  <div class="toolbar">
    <select v-if="tables?.length" v-model="table" class="select" aria-label="Stick table"><option v-for="t in tables" :key="t.name" :value="t.name">{{ t.name }}</option></select>
    <span v-if="current" class="hint">{{ current.type }} · {{ num(current.used) }} of {{ num(current.size) }} entries used · refreshes every 5 s</span>
    <span class="grow" /><label class="search"><Icon name="search" /><input v-model="q" class="input" placeholder="Filter keys (e.g. an IP)" aria-label="Filter"></label>
  </div>
  <div v-if="error" class="banner show"><Icon name="crit" /><span class="msg">{{ error }}</span></div>
  <section class="card flush"><div class="card-body table-wrap">
    <EmptyState v-if="tables && !tables.length" title="No stick tables" icon="db" sub="Turn on a rate limit on a frontend (Frontends → Edit) and its table of client IPs shows up here." />
    <EmptyState v-else-if="entries && !entries.length" title="The table is empty" icon="db" sub="Entries appear as clients send requests." />
    <table v-else-if="entries" class="data"><thead><tr><th>Key</th><th v-for="c in cols" :key="c" class="num">{{ c.replace(/_/g, " ") }}</th><th class="num">Expires in</th><th v-if="canOperate" class="actions"><span class="sr-only">Actions</span></th></tr></thead>
      <tbody><tr v-for="e in rows.slice(0, 200)" :key="e.key"><td class="primary mono">{{ e.key }}</td>
        <td v-for="c in cols" :key="c" class="num">{{ num(e[c]) }}</td><td class="num dim">{{ e.exp != null ? `${Math.round(e.exp / 1000)} s` : "–" }}</td>
        <td v-if="canOperate" class="actions"><button v-if="limited(e)" class="btn sm" type="button" @click="unblock(e)">Unblock</button></td></tr></tbody></table>
  </div></section>
  <p class="hint" style="margin-top:12px">Entries expire by themselves. <b>Unblock</b> resets one client's rates to 0, so a rate limit lets it in again immediately.</p>
</template>
