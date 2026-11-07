<script setup lang="ts">
// Where the activity log is copied to (webhook, syslog, files) and how long the database keeps it.
import { onMounted, reactive } from "vue";
import { hub } from "@/api/client";
import Icon from "@/components/Icon.vue";
import PasswordInput from "@/components/PasswordInput.vue";
import { toast } from "@/stores/ui";

const f = reactive({ loaded: false, retention_days: 0, webhook_enabled: false, webhook_url: "", webhook_secret: "", webhook_secret_set: false,
  syslog_enabled: false, syslog_host: "", syslog_port: 514, syslog_protocol: "udp", file_enabled: false, file_keep_days: 30,
  file_folder: "", sent: 0, error: "", busy: false, msg: "", ok: true });
async function load() { try { Object.assign(f, await hub.get("/settings/audit-export"), { webhook_secret: "", loaded: true }); } catch { /* older hub */ } }
onMounted(load);
async function save(thenTest = false) {
  f.busy = true; f.msg = "";
  try {
    Object.assign(f, await hub.put("/settings/audit-export", { retention_days: f.retention_days, webhook_enabled: f.webhook_enabled, webhook_url: f.webhook_url,
      webhook_secret: f.webhook_secret || null, syslog_enabled: f.syslog_enabled, syslog_host: f.syslog_host, syslog_port: Number(f.syslog_port) || 514,
      syslog_protocol: f.syslog_protocol, file_enabled: f.file_enabled, file_keep_days: f.file_keep_days }), { webhook_secret: "" });
    if (thenTest) { const r = await hub.post<any>("/settings/audit-export/test", {}); f.ok = true; f.msg = r.message; } else toast(true, "Activity log settings saved");
  } catch (e: any) { f.ok = false; f.msg = e.message; } finally { f.busy = false; }
}
const RETENTION: [number, string][] = [[0, "Forever"], [90, "90 days"], [180, "180 days"], [365, "1 year"], [730, "2 years"]];
</script>

<template>
  <div class="section-bar"><h2>Activity log export</h2><span class="hint">Copy every activity entry to your SIEM or log system as it happens, and choose how long k10y keeps it.</span></div>
  <section v-if="f.loaded" class="card" style="margin-bottom:28px"><div class="card-body ae">
    <div class="pref-row"><div class="lbl">Keep in k10y<div>Older entries are removed from the database once an hour. Exported copies aren't affected.</div></div>
      <div class="segmented" role="group"><button v-for="[d, l] in RETENTION" :key="d" type="button" :aria-pressed="f.retention_days === d" @click="f.retention_days = d">{{ l }}</button></div></div>

    <label class="pill ae-head"><input v-model="f.webhook_enabled" type="checkbox"> <b>Webhook</b> <span class="dim">JSON batches every few seconds (Splunk HEC, Elastic, Datadog, your own service)</span></label>
    <div v-if="f.webhook_enabled" class="ae-sub">
      <div class="form-row"><label class="lbl" for="ae-url">URL</label><input id="ae-url" v-model="f.webhook_url" class="input mono" placeholder="https://logs.example.com/k10y"></div>
      <div class="form-row"><label class="lbl">Signing secret (optional)</label><PasswordInput v-model="f.webhook_secret" autocomplete="off" mono :placeholder="f.webhook_secret_set ? 'Saved. Leave empty to keep it' : ''" />
        <span class="hint">Body: <code>{"entries": [...]}</code>. With a secret, <code>X-K10y-Signature: sha256=…</code> is the HMAC-SHA256 of the body.</span></div>
    </div>

    <label class="pill ae-head"><input v-model="f.syslog_enabled" type="checkbox"> <b>Syslog</b> <span class="dim">RFC 5424, the entry as JSON (rsyslog, syslog-ng, most SIEMs)</span></label>
    <div v-if="f.syslog_enabled" class="ae-sub">
      <div class="form-row"><label class="lbl">Server</label><div class="field"><input v-model="f.syslog_host" class="input" placeholder="siem.example.com" style="flex:2" aria-label="Syslog server">
        <input v-model.number="f.syslog_port" class="input" type="number" min="1" max="65535" aria-label="Port" style="max-width:100px">
        <select v-model="f.syslog_protocol" class="select" aria-label="Protocol" style="max-width:140px"><option value="udp">UDP</option><option value="tcp">TCP</option><option value="tls">TLS</option></select></div></div>
    </div>

    <label class="pill ae-head"><input v-model="f.file_enabled" type="checkbox"> <b>Files</b> <span class="dim">one JSON line per entry, a file per day, in <code>{{ f.file_folder }}</code></span></label>
    <div v-if="f.file_enabled" class="ae-sub"><div class="form-row"><label class="lbl" for="ae-keep">Delete files after (days, 0 = never)</label>
      <input id="ae-keep" v-model.number="f.file_keep_days" class="input" type="number" min="0" style="max-width:120px"></div></div>

    <p v-if="f.sent || f.error" class="hint" style="margin:0">{{ f.sent }} entries exported since the hub started.<span v-if="f.error" style="color:var(--critical)"> Last problem: {{ f.error }}</span></p>
    <div v-if="f.msg" class="test-result show" :class="f.ok ? 'ok' : 'err'"><Icon :name="f.ok ? 'ok' : 'crit'" /><span>{{ f.msg }}</span></div>
    <div class="ae-row"><span class="grow" />
      <button class="btn" type="button" :disabled="f.busy || !(f.webhook_enabled || f.syslog_enabled || f.file_enabled)" @click="save(true)">Save &amp; send test</button>
      <button class="btn primary" type="button" :disabled="f.busy" @click="save()">Save</button></div>
  </div></section>
</template>

<style scoped>
.ae { display: flex; flex-direction: column; gap: 12px; }
.ae-head { align-self: flex-start; gap: 8px; }
.ae-sub { display: flex; flex-direction: column; gap: 10px; padding: 0 0 4px 28px; }
.ae-row { display: flex; gap: 8px; align-items: center; }
</style>
