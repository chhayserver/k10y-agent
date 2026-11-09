<script setup lang="ts">
// Connect Nginx instances: each runs the k10y agent in Nginx mode; the hub stores its address and token.
import { onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { hub } from "@/api/client";
import Icon from "@/components/Icon.vue";
import Modal from "@/components/Modal.vue";
import PasswordInput from "@/components/PasswordInput.vue";
import Skeleton from "@/components/Skeleton.vue";
import { fmt } from "@/lib/format";
import { to } from "@/lib/nav";
import { useNginx } from "@/stores/nginx";
import { useSession } from "@/stores/session";
import { toast } from "@/stores/ui";

const n = useNginx(), s = useSession(), router = useRouter();
const list = ref<any[] | null>(null);
async function load() { list.value = await hub.get("/nginx"); }
onMounted(load);
const e = reactive({ open: false, id: 0, name: "", url: "", token: "", verify_tls: false, group_name: "", busy: false, msg: "", ok: true });
function openNew() { Object.assign(e, { open: true, id: 0, name: "", url: "", token: "", verify_tls: false, group_name: "", msg: "" }); }
function openEdit(i: any) { Object.assign(e, { open: true, id: i.id, name: i.name, url: i.url, token: "", verify_tls: i.verify_tls, group_name: i.group_name, msg: "" }); }
async function test() {
  e.busy = true; e.msg = "";
  try {
    const r = await hub.post<any>("/nginx/test", { url: e.url, token: e.token || null, verify_tls: e.verify_tls, instance_id: e.id || null });
    e.url = r.url; e.ok = true;
    e.msg = `Connected: Nginx ${r.nginx_version || "?"}${r.running ? "" : " (not running!)"} · agent ${r.agent_version} · ${r.files.length} config file(s)`;
    if (!e.name) e.name = String(r.name || "").replace(/[^A-Za-z0-9._-]/g, "-");
    return true;
  } catch (err: any) { e.ok = false; e.msg = err.message; return false; } finally { e.busy = false; }
}
async function save() {
  if (!(await test())) return;
  e.busy = true;
  try {
    const body = { name: e.name, url: e.url, token: e.token || null, verify_tls: e.verify_tls, group_name: e.group_name };
    const saved = e.id ? await hub.patch<any>(`/nginx/${e.id}`, body) : await hub.post<any>("/nginx", body);
    e.open = false; toast(true, `Saved ${saved.name}`); await load(); await s.load(); await n.loadInstances(saved.id);
  } catch (err: any) { e.ok = false; e.msg = err.message; } finally { e.busy = false; }
}
async function remove(i: any) {
  if (!confirm(`Remove ${i.name} from k10y? Nginx and its agent aren't changed.`)) return;
  try { await hub.del(`/nginx/${i.id}`); toast(true, `Removed ${i.name}`); await load(); await s.load(); await n.loadInstances(); } catch (err: any) { toast(false, err.message); }
}
function open(i: any) { n.select(i.id); router.push(to("nginx-overview")); }
const tokenCmd = "docker exec <nginx-container> cat /var/lib/k10y/token    # or on a server: sudo cat /etc/k10y/nginx-token";
</script>

<template>
  <div class="toolbar"><span class="hint">Each Nginx runs the k10y agent (the <code>k10y/k10y-nginx</code> image, or the agent installed on the server). Add its address and token here.</span>
    <span class="grow" /><button v-if="s.canAll('nginx.manage')" class="btn primary" type="button" @click="openNew"><Icon name="nginx" /><span>Add Nginx</span></button></div>
  <section class="card flush"><div class="card-body table-wrap">
    <Skeleton v-if="!list" :rows="3" />
    <div v-else-if="!list.length" class="empty"><Icon name="nginx" /><div class="title">No Nginx yet</div>
      <div class="s">Start the agent next to Nginx, then add it. See <a href="https://k10y-agents.pages.dev/docs/nginx" target="_blank" rel="noopener">Nginx in the docs</a>.</div></div>
    <table v-else class="data"><thead><tr><th>Name</th><th>Agent</th><th>Group</th><th>Added</th><th class="actions"><span class="sr-only">Actions</span></th></tr></thead>
      <tbody><tr v-for="i in list" :key="i.id" class="clickable" @click="(ev) => !(ev.target as HTMLElement).closest('button') && openEdit(i)">
        <td class="primary">{{ i.name }}</td><td class="mono">{{ i.url }}</td><td>{{ i.group_name || "–" }}</td><td class="dim">{{ fmt.dateTime(i.created_at) }}</td>
        <td class="actions"><button class="btn ghost sm" type="button" @click="open(i)">Open</button>
          <button class="btn ghost sm" style="color:var(--critical)" type="button" @click="remove(i)">Remove</button></td></tr></tbody></table>
  </div></section>

  <Modal :open="e.open" :title="e.id ? `Edit ${e.name}` : 'Add Nginx'" @close="e.open = false">
    <div class="form-row"><label class="lbl" for="ng-url">Agent address</label><input id="ng-url" v-model="e.url" class="input mono" placeholder="web1.example.com or 10.0.0.5:9443">
      <span class="hint">The agent serves HTTPS on port 9443; just the host name is enough.</span></div>
    <div class="form-row"><label class="lbl">Agent token</label><PasswordInput v-model="e.token" autocomplete="off" mono :placeholder="e.id ? 'Saved. Leave empty to keep it' : ''" />
      <span class="hint">Created by the agent on first start: <code>{{ tokenCmd }}</code></span></div>
    <div class="form-row"><label class="lbl" for="ng-name">Name</label><input id="ng-name" v-model="e.name" class="input" placeholder="e.g. web1"></div>
    <div class="form-row"><label class="lbl" for="ng-group">Group (optional)</label><input id="ng-group" v-model="e.group_name" class="input" placeholder="e.g. edge"></div>
    <label class="pill" style="align-self:flex-start"><input v-model="e.verify_tls" type="checkbox"> Verify the agent's TLS certificate (off for its self-signed one)</label>
    <div v-if="e.msg" class="test-result show" :class="e.ok ? 'ok' : 'err'"><Icon :name="e.ok ? 'ok' : 'crit'" /><span>{{ e.msg }}</span></div>
    <template #actions><button class="btn ghost" type="button" @click="e.open = false">Cancel</button><span style="flex:1" />
      <button class="btn" type="button" :disabled="e.busy || !e.url" @click="test">Test</button>
      <button class="btn primary" type="button" :disabled="e.busy || !e.url || (!e.id && !e.token)" @click="save">Save</button></template>
  </Modal>
</template>
