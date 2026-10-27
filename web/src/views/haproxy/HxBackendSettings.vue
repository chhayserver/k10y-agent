<script setup lang="ts">
// Backend settings in plain words: balancing method (every HAProxy algorithm), health check (HTTP, databases,
// protocols), sticky sessions, client IP header and timeouts. Edits the object passed in v-model.
import { computed } from "vue";
import { BALANCE_INFO, CHECK_INFO, balanceInfo, checkInfo, type Settings } from "@/lib/haproxyCatalog";

const s = defineModel<Settings>({ required: true });
defineProps<{ hideMode?: boolean; compact?: boolean; hideCheck?: boolean }>();

const http = computed(() => s.value.mode === "http");
const balances = computed(() => BALANCE_INFO.filter((b) => (http.value ? !b.tcp : !b.http) || b.value === s.value.balance));
const checks = computed(() => CHECK_INFO.filter((c) => ((http.value ? !c.tcp : !c.http) && c.value !== "tcp") || c.value === s.value.check.type));
const bal = computed(() => balanceInfo(s.value.balance));
const chk = computed(() => checkInfo(s.value.check.type));
function setMode(m: string) {
  s.value.mode = m;
  if (m === "tcp") {
    if (balanceInfo(s.value.balance).http) s.value.balance = "leastconn";
    if (s.value.check.type === "http") s.value.check = { type: "connect" };
    s.value.sticky = false; s.value.forwardfor = false;
  } else if (checkInfo(s.value.check.type).tcp) s.value.check = { type: "http", method: "GET", uri: "/health" };
}
function setCheck(t: string) {
  s.value.check = t === "http" ? { type: "http", method: "GET", uri: s.value.check.uri || "/health", host: "" }
    : { type: t, user: t === "pgsql" || t === "mysql" ? s.value.check.user || "haproxy_check" : "" };
}
</script>

<template>
  <div class="hxs">
    <div v-if="!hideMode" class="form-row"><label class="lbl">Protocol</label>
      <div class="segmented" role="group" aria-label="Protocol">
        <button type="button" :aria-pressed="s.mode === 'http'" @click="setMode('http')">HTTP</button>
        <button type="button" :aria-pressed="s.mode === 'tcp'" @click="setMode('tcp')">TCP</button></div>
      <span class="hint">{{ http ? "Web traffic: HAProxy reads requests, so it can route by path, add headers and use cookies." : "Any protocol (databases, Redis, MQTT…): HAProxy passes the connection through unchanged." }}</span></div>

    <div class="hxs-grid">
      <div class="form-row"><label class="lbl" for="hxs-bal">Balancing</label>
        <select id="hxs-bal" v-model="s.balance" class="select"><option v-for="b in balances" :key="b.value" :value="b.value">{{ b.label }}</option></select>
        <span class="hint">{{ bal.hint }}</span></div>
      <div v-if="bal.arg" class="form-row"><label class="lbl" for="hxs-arg">{{ bal.arg.label }}</label>
        <input id="hxs-arg" v-model="s.balance_arg" class="input mono" :placeholder="bal.arg.placeholder"></div>
    </div>

    <div v-if="!hideCheck" class="hxs-grid">
      <div class="form-row"><label class="lbl" for="hxs-chk">Health check</label>
        <select id="hxs-chk" :value="s.check.type === 'default' ? 'connect' : s.check.type" class="select" @change="setCheck(($event.target as HTMLSelectElement).value)">
          <option v-for="c in checks" :key="c.value" :value="c.value">{{ c.label }}</option></select>
        <span class="hint">{{ chk.hint }}</span></div>
      <div v-if="s.check.type === 'http'" class="form-row"><label class="lbl">Request</label>
        <div class="hxs-inline"><select v-model="s.check.method" class="select" style="max-width:100px" aria-label="Method"><option>GET</option><option>HEAD</option><option>OPTIONS</option></select>
          <input v-model="s.check.uri" class="input mono" placeholder="/health" aria-label="Path"></div>
        <input v-if="!compact" v-model="s.check.host" class="input mono" placeholder="Host header (optional), e.g. api.example.com" aria-label="Host header"></div>
      <div v-else-if="chk.user" class="form-row"><label class="lbl" for="hxs-user">Check user</label>
        <input id="hxs-user" v-model="s.check.user" class="input mono" placeholder="haproxy_check">
        <span class="hint">{{ chk.user }}</span></div>
    </div>

    <details v-if="!compact" class="hxs-adv" :open="s.sticky || s.forwardfor || !!(s.timeouts.connect || s.timeouts.server || s.timeouts.tunnel)">
      <summary>Sessions, headers and timeouts</summary>
      <div v-if="http" class="hxs-checks">
        <label class="pill"><input v-model="s.sticky" type="checkbox"> Sticky sessions (cookie)</label>
        <label class="pill"><input v-model="s.forwardfor" type="checkbox"> Pass the client IP (X-Forwarded-For)</label></div>
      <p v-if="http" class="hint" style="margin:6px 0 0">Sticky sessions keep a user on the same server with a <code>SERVERID</code> cookie: needed for apps that keep sessions in memory.</p>
      <div class="hxs-times">
        <label class="form-row"><span class="lbl">Connect</span><span class="hxs-inline"><input v-model.number="s.timeouts.connect" class="input" type="number" min="0" placeholder="5"><span class="hint">s</span></span></label>
        <label class="form-row"><span class="lbl">Server reply</span><span class="hxs-inline"><input v-model.number="s.timeouts.server" class="input" type="number" min="0" :placeholder="http ? '30' : '1800'"><span class="hint">s</span></span></label>
        <label class="form-row"><span class="lbl">{{ http ? "WebSocket / tunnel" : "Idle tunnel" }}</span><span class="hxs-inline"><input v-model.number="s.timeouts.tunnel" class="input" type="number" min="0" placeholder="3600"><span class="hint">s</span></span></label>
      </div>
      <p class="hint" style="margin:0">Empty or 0 uses the defaults from the config. Databases and WebSocket need long timeouts (e.g. 1800–3600 s); APIs 30–60 s.</p>
    </details>
  </div>
</template>

<style scoped>
.hxs { display: flex; flex-direction: column; gap: 12px; }
.hxs-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; align-items: start; }
.hxs-inline { display: flex; gap: 8px; align-items: center; }
.mono { font-family: var(--mono); }
.hxs-adv { border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; }
.hxs-adv summary { cursor: pointer; font-weight: 550; font-size: 13.5px; }
.hxs-adv[open] summary { margin-bottom: 10px; }
.hxs-checks { display: flex; gap: 8px; flex-wrap: wrap; }
.hxs-times { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin: 10px 0 6px; }
.hxs-times .input { max-width: 110px; }
</style>
