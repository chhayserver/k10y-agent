<script setup lang="ts">
// Live HAProxy request logs, received by the hub over syslog (UDP).
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import Icon from "@/components/Icon.vue";
import { hx, type HxOp } from "@/lib/haproxy";

const props = defineProps<{ instanceId: number; canEdit: boolean }>();
const emit = defineEmits<{ review: [ops: HxOp[], title: string] }>();
const info = ref<{ enabled: boolean; port: number | null; tag: string; other_tags: string[] } | null>(null);
const lines = ref<any[]>([]), paused = ref(false), q = ref(""), cls = ref("all"), error = ref("");
let after = 0, timer = 0;
async function poll() {
  if (paused.value || document.hidden) return;
  try {
    const r = await hx.logs(props.instanceId, after);
    info.value = r; error.value = "";
    if (r.lines.length) { after = r.lines[r.lines.length - 1].seq; lines.value = lines.value.concat(r.lines).slice(-2000); }
  } catch (e: any) { error.value = e.message; }
}
onMounted(() => { poll(); timer = window.setInterval(poll, 2000); });
onBeforeUnmount(() => clearInterval(timer));
const shown = computed(() => {
  const s = q.value.trim().toLowerCase();
  return lines.value.filter((l) => (cls.value === "all" || (l.status != null && String(l.status)[0] === cls.value[0]) || (cls.value === "other" && l.status == null))
    && (!s || JSON.stringify(l).toLowerCase().includes(s))).slice(-500).reverse();
});
const statusColor = (s: number) => (s >= 500 ? "var(--critical)" : s >= 400 ? "#e8a33a" : s >= 300 ? "var(--accent-ink)" : "var(--good)");
const setup = reactive({ open: false, address: `${location.hostname === "localhost" ? "host.docker.internal" : location.hostname}:5514` });
async function enable() {
  // Add the two lines to the global section through the normal review flow.
  const r = await hx.config(props.instanceId);
  const tag = info.value?.tag || "haproxy";
  const lines = r.text.split("\n");
  const g = lines.findIndex((l) => /^global\b/.test(l));
  if (g < 0) return;
  const add = [`  log ${setup.address} len 4096 local0`, `  log-tag ${tag}`].filter((l) => !lines.some((x) => x.trim() === l.trim()));
  lines.splice(g + 1, 0, ...add);
  setup.open = false;
  emit("review", [{ op: "raw", kind: "config", data: { text: lines.join("\n") } }], "Send HAProxy logs to k10y");
}
</script>
<template>
  <div v-if="info && !info.enabled" class="banner show"><Icon name="warn" /><span class="msg">The hub isn't listening for logs. Set <code>K10Y_SYSLOG_PORT=5514</code> on the hub and publish <code>5514/udp</code> (the provided docker-compose.yml does both).</span></div>
  <div v-else-if="info && !lines.length" class="card" style="padding:16px 18px;margin-bottom:14px">
    <b>No logs received from this HAProxy yet.</b>
    <p class="hint" style="margin:6px 0 10px">HAProxy must send its logs to the hub with the tag <code>{{ info.tag }}</code>:</p>
    <pre class="logs" style="max-height:none">global
    log {{ setup.address }} len 4096 local0
    log-tag {{ info.tag }}</pre>
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:10px">
      <label class="lbl" style="margin:0">Hub address as seen from HAProxy</label><input v-model="setup.address" class="input" style="max-width:280px;font-family:var(--mono)">
      <button v-if="canEdit" class="btn primary" @click="enable">Add this to the config…</button></div>
    <p v-if="info.other_tags.length" class="hint" style="margin:10px 0 0">Logs are arriving with other tags: <code>{{ info.other_tags.join(", ") }}</code>. Set the instance's log tag in Administration → HAProxy instances to match.</p>
  </div>
  <div class="toolbar">
    <label class="search"><Icon name="search" /><input v-model="q" class="input" placeholder="Filter: client, path, server, status…" aria-label="Filter"></label>
    <div class="segmented" role="group"><button v-for="c in ['all', '2xx', '3xx', '4xx', '5xx', 'other']" :key="c" :aria-pressed="cls === c" @click="cls = c">{{ c === "all" ? "All" : c === "other" ? "Events" : c }}</button></div>
    <span class="grow" /><span class="hint">{{ shown.length }} shown · {{ lines.length }} received</span>
    <button class="btn" :aria-pressed="paused" @click="paused = !paused"><Icon :name="paused ? 'play' : 'pause'" /><span>{{ paused ? "Resume" : "Pause" }}</span></button>
    <button class="btn" @click="lines = []">Clear</button>
  </div>
  <div v-if="error" class="banner show"><Icon name="crit" /><span class="msg">{{ error }}</span></div>
  <section class="card flush"><div class="card-body table-wrap">
    <table class="data hx-logs"><thead><tr><th>Time</th><th>Status</th><th>Request</th><th>Frontend → server</th><th class="num">Duration</th><th class="num">Bytes</th><th>Client</th></tr></thead>
      <tbody>
        <tr v-if="!shown.length"><td colspan="7" class="dim">{{ lines.length ? "No lines match the filter." : "Waiting for log lines…" }}</td></tr>
        <tr v-for="l in shown" :key="l.seq">
          <td class="dim mono">{{ new Date(l.t).toLocaleTimeString() }}</td>
          <template v-if="l.status != null">
            <td><b :style="{ color: statusColor(l.status) }">{{ l.status }}</b></td>
            <td class="mono wrap">{{ l.request }}</td>
            <td class="mono">{{ l.frontend }} → {{ l.backend }}/{{ l.server }}</td>
            <td class="num">{{ l.ms }} ms</td><td class="num">{{ l.bytes }}</td><td class="mono dim">{{ l.client }}</td>
          </template>
          <td v-else colspan="6" class="mono wrap">{{ l.msg }}</td>
        </tr></tbody></table>
  </div></section>
</template>
