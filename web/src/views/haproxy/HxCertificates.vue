<script setup lang="ts">
// TLS certificates stored on this HAProxy (Data Plane API storage): expiry, domains, which frontends use them.
import { computed, onMounted, reactive, ref } from "vue";
import Badge from "@/components/Badge.vue";
import EmptyState from "@/components/EmptyState.vue";
import Icon from "@/components/Icon.vue";
import Modal from "@/components/Modal.vue";
import { fmt, type Level } from "@/lib/format";
import { hx } from "@/lib/haproxy";
import { toast } from "@/stores/ui";

const props = defineProps<{ instanceId: number; canEdit: boolean }>();
const certs = ref<any[] | null>(null), error = ref("");
async function load() { try { certs.value = await hx.certificates(props.instanceId); error.value = ""; } catch (e: any) { error.value = e.message; } }
onMounted(load);
const up = reactive({ open: false, name: "", pem: "", busy: false, err: "", replace: false });
function openUpload(name = "") { Object.assign(up, { open: true, name, pem: "", busy: false, err: "", replace: !!name }); }
async function readFile(e: Event) {
  const files = (e.target as HTMLInputElement).files;
  if (!files) return;
  let text = "";
  for (const f of Array.from(files)) text += (await f.text()).trim() + "\n";
  up.pem = text;
  if (!up.name && files[0]) up.name = files[0].name.replace(/\.(crt|cer|key)$/i, ".pem");
}
async function upload() {
  up.busy = true; up.err = "";
  try { const r: any = await hx.uploadCert(props.instanceId, up.name.trim(), up.pem); toast(true, `${r.message}. HAProxy reloaded.`); up.open = false; load(); }
  catch (e: any) { up.err = e.message; } finally { up.busy = false; }
}
async function remove(c: any) {
  if (!confirm(`Delete certificate ${c.name}?`)) return;
  try { await hx.deleteCert(props.instanceId, c.name); toast(true, `Deleted ${c.name}`); load(); } catch (e: any) { toast(false, e.message); }
}
function expiry(c: any): [Level, string] {
  const d = c.days_left;
  if (d == null) return ["neutral", "Unknown"];
  if (d < 0) return ["critical", "Expired"];
  if (d < 14) return ["critical", `${Math.floor(d)} days left`];
  if (d < 30) return ["warning", `${Math.floor(d)} days left`];
  return ["good", `${Math.floor(d)} days left`];
}
const soonest = computed(() => (certs.value || []).filter((c) => c.days_left != null).sort((a, b) => a.days_left - b.days_left)[0]);
</script>
<template>
  <div class="toolbar"><span class="hint">Certificates are stored on the HAProxy through its Data Plane API. Use them for TLS listeners on the <b>Frontends</b> page.
    <template v-if="soonest"> Next expiry: <b>{{ soonest.name }}</b> on {{ fmt.dateTime(soonest.not_after) }}.</template></span><span class="grow" />
    <button class="btn" @click="load"><Icon name="refresh" /><span>Reload</span></button>
    <button v-if="canEdit" class="btn primary" @click="openUpload()"><Icon name="upload" /><span>Upload certificate</span></button></div>
  <div v-if="error" class="banner show"><Icon name="crit" /><span class="msg">{{ error }}</span></div>
  <section class="card flush"><div class="card-body table-wrap">
    <EmptyState v-if="certs && !certs.length" title="No certificates yet" icon="lock" sub="Upload a certificate and its private key to serve HTTPS." />
    <table v-else-if="certs" class="data"><thead><tr><th>Certificate</th><th>Expires</th><th>Domains</th><th>Issuer</th><th>Used by</th><th class="actions"><span class="sr-only">Actions</span></th></tr></thead>
      <tbody><tr v-for="c in certs" :key="c.name">
        <td class="primary"><span class="mono">{{ c.name }}</span><div class="dim" style="font-size:12px">{{ c.subject }}</div></td>
        <td><Badge :level="expiry(c)[0]" :label="expiry(c)[1]" /><div class="dim" style="font-size:12px">{{ fmt.dateTime(c.not_after) }}</div></td>
        <td class="wrap mono" style="font-size:12.5px">{{ c.domains || "–" }}</td>
        <td class="dim">{{ c.issuer || "–" }}</td>
        <td class="wrap"><span v-for="u in c.used_by" :key="u" class="chip">{{ u }}</span><span v-if="!c.used_by.length" class="dim">not used</span></td>
        <td class="actions"><template v-if="canEdit"><button class="btn ghost sm" @click="openUpload(c.name)">Replace</button>
          <button class="btn ghost sm" style="color:var(--critical)" :disabled="c.used_by.length > 0" :title="c.used_by.length ? 'Still used by a frontend' : ''" @click="remove(c)">Delete</button></template></td>
      </tr></tbody></table>
  </div></section>
  <p class="hint" style="margin-top:12px">Add an alert rule <b>HAProxy → certificate expiring</b> to get a Telegram message before a certificate expires.</p>
  <Modal :open="up.open" :title="up.replace ? `Replace ${up.name}` : 'Upload certificate'" wide @close="up.open = false">
    <p class="hint" style="margin:0">Paste the certificate chain and its private key in PEM format (one after the other), or choose the files. Replacing a certificate that's in use reloads HAProxy gracefully.</p>
    <div class="form-row"><label class="lbl">Name</label><input v-model="up.name" class="input" :disabled="up.replace" placeholder="shop.example.com.pem" style="font-family:var(--mono)"></div>
    <div class="form-row"><label class="lbl">Files</label><input type="file" accept=".pem,.crt,.cer,.key" multiple @change="readFile"></div>
    <div class="form-row"><label class="lbl">PEM</label><textarea v-model="up.pem" class="ye-text" style="min-height:220px" spellcheck="false" placeholder="-----BEGIN CERTIFICATE-----&#10;…&#10;-----BEGIN PRIVATE KEY-----&#10;…" /></div>
    <div v-if="up.err" class="test-result show err"><Icon name="crit" /><span>{{ up.err }}</span></div>
    <template #actions><button class="btn ghost" type="button" @click="up.open = false">Cancel</button><span style="flex:1" />
      <button class="btn primary" type="button" :disabled="up.busy || !up.name || !up.pem" @click="upload">{{ up.busy ? "Uploading…" : up.replace ? "Replace" : "Upload" }}</button></template>
  </Modal>
</template>
