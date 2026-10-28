<script setup lang="ts">
// IPs refused on every frontend. The first block sets the list up (one reload); after that adding and
// removing addresses is instant, with no reload, and the list is kept for future reloads.
import { onMounted, ref } from "vue";
import Icon from "@/components/Icon.vue";
import { hx } from "@/lib/haproxy";
import { openAction, toast } from "@/stores/ui";

const props = defineProps<{ instanceId: number; canOperate: boolean; canConfigure?: boolean }>();
const list = ref<Awaited<ReturnType<typeof hx.blocklist>> | null>(null), error = ref(""), ip = ref(""), reason = ref("");
async function load() { try { list.value = await hx.blocklist(props.instanceId); error.value = ""; } catch (e: any) { error.value = e.message; } }
onMounted(load);
defineExpose({ load });

function add() {
  const v = ip.value.trim();
  if (!/^[0-9a-fA-F:.]+(\/\d{1,3})?$/.test(v)) { toast(false, "Enter an IP address or a network, e.g. 203.0.113.7 or 203.0.113.0/24"); return; }
  const first = !list.value?.ready;
  openAction({ title: `Block ${v}`, confirmLabel: "Block", noDryRun: true,
    desc: first ? "This is the first block on this HAProxy: k10y adds the blocklist to every frontend, which needs one reload (seamless, about two seconds). After that, blocking and unblocking are instant."
      : "Connections from this address are refused on every frontend right away, without a reload.",
    run: async () => { const r = await hx.block(props.instanceId, v, reason.value.trim()); ip.value = ""; reason.value = ""; await load(); return r.message; } });
}
function remove(v: string) {
  openAction({ title: `Unblock ${v}`, confirmLabel: "Unblock", noDryRun: true, desc: "Connections from this address are accepted again right away, without a reload.",
    run: async () => { const r = await hx.unblock(props.instanceId, v); await load(); return r.message; } });
}
</script>

<template>
  <section class="card hxbl">
    <div class="card-head"><h2>Blocked IPs</h2><span class="hint">Refused on every frontend. Changes apply instantly, without a reload.</span></div>
    <div class="card-body">
      <div v-if="error" class="test-result show err"><Icon name="crit" /><span>{{ error }}</span></div>
      <p v-if="canOperate && list && !list.ready && !canConfigure" class="hint" style="margin:0">The first block on this HAProxy adds the blocklist to every frontend (a config change), so someone with <b>haproxy.configure</b> has to do it once. After that you can block and unblock here.</p>
      <form v-if="canOperate && (list?.ready || canConfigure)" class="hxbl-add" @submit.prevent="add">
        <input v-model="ip" class="input mono" placeholder="203.0.113.7 or 203.0.113.0/24" aria-label="IP or network to block">
        <input v-model="reason" class="input" placeholder="Reason (optional), e.g. scraping /api/login" aria-label="Reason">
        <button class="btn" type="submit"><Icon name="ban" /><span>Block</span></button></form>
      <p v-if="list && !list.ips.length" class="hint" style="margin:0">Nothing is blocked. You can also block a client from the <b>Logs</b> page.</p>
      <ul v-else-if="list" class="hxbl-list">
        <li v-for="v in list.ips" :key="v"><Icon name="ban" /><span class="mono">{{ v }}</span>
          <button v-if="canOperate" class="link" type="button" @click="remove(v)">Unblock</button></li></ul>
      <p v-if="list?.ready && list.missing.length" class="hint" style="margin:0">Not protected yet: {{ list.missing.join(", ") }} (the next block adds them).</p>
    </div>
  </section>
</template>

<style scoped>
.hxbl { margin-bottom: 16px; }
.hxbl .card-body { display: flex; flex-direction: column; gap: 12px; }
.hxbl-add { display: flex; gap: 8px; flex-wrap: wrap; }
.hxbl-add .input { flex: 1; min-width: 200px; }
.mono { font-family: var(--mono); }
.hxbl-list { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 8px; }
.hxbl-list li { display: flex; align-items: center; gap: 8px; padding: 5px 10px; border: 1px solid var(--border); border-radius: 999px; font-size: 13px; }
.hxbl-list .icon { width: 13px; height: 13px; color: var(--critical); }
</style>
