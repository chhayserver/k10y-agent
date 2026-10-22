<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { hub } from "@/api/client";
import DataTable from "@/components/DataTable.vue";
import Icon from "@/components/Icon.vue";
import Modal from "@/components/Modal.vue";
import Skeleton from "@/components/Skeleton.vue";
import { fmt } from "@/lib/format";
import { refreshFleet } from "@/stores/fleet";
import { page } from "@/stores/prefs";
import { useSession } from "@/stores/session";
import { toast } from "@/stores/ui";
const s = useSession(), state = page("manage-clusters", { key: "name", dir: 1 });
const list = ref<any[] | null>(null);
async function load() { list.value = await hub.get("/clusters"); }
onMounted(load);
const edit = reactive({ open: false, id: 0, name: "", url: "https://", token: "", verify_tls: false, test: null as null | { ok: boolean; text: string }, busy: false });
function openNew() { Object.assign(edit, { open: true, id: 0, name: "", url: "https://", token: "", verify_tls: false, test: null }); }
function openEdit(c: any) { Object.assign(edit, { open: true, id: c.id, name: c.name, url: c.url, token: "", verify_tls: c.verify_tls, test: null }); }
async function test() {
  edit.busy = true; edit.test = null;
  try { const i = await hub.post("/clusters/test", { url: edit.url, token: edit.token || null, verify_tls: edit.verify_tls, cluster_id: edit.id || null }); edit.test = { ok: true, text: `Connected to ${i.cluster} · ${i.kubernetes_version} · agent v${i.agent_version}${i.control_enabled ? "" : " · read-only"}` }; if (!edit.name) edit.name = i.cluster; return true; }
  catch (e: any) { edit.test = { ok: false, text: e.message }; return false; } finally { edit.busy = false; }
}
async function save() {
  if (!(await test())) return;
  edit.busy = true;
  try {
    const body = { name: edit.name, url: edit.url, token: edit.token || null, verify_tls: edit.verify_tls };
    if (edit.id) await hub.patch(`/clusters/${edit.id}`, body); else await hub.post("/clusters", body);
    edit.open = false; toast(true, `Saved cluster ${edit.name}`); await load(); await s.load(); refreshFleet();
  } catch (e: any) { edit.test = { ok: false, text: e.message }; } finally { edit.busy = false; }
}
async function remove(c: any) {
  if (!confirm(`Remove cluster ${c.name} from the hub? The cluster and its agent are not changed.`)) return;
  try { await hub.del(`/clusters/${c.id}`); toast(true, `Removed ${c.name}`); await load(); await s.load(); } catch (e: any) { toast(false, e.message); }
}
const columns = [{ key: "name", label: "Name", cls: "primary" }, { key: "url", label: "Agent URL", cls: "mono" }, { key: "verify_tls", label: "TLS" },
  { key: "created_at", label: "Added" }, { key: "_a", label: "Actions", nosort: true, cls: "actions" }];
</script>
<template>
  <div class="toolbar"><span class="hint">Each cluster runs a k10y agent. Install it with <code>kubectl apply -f install.yaml</code>, then add its URL and token here.</span><span class="grow" />
    <button class="btn primary" @click="openNew"><Icon name="upload" /><span>Add cluster</span></button></div>
  <section class="card flush"><div class="card-body table-wrap">
    <Skeleton v-if="!list" :rows="3" />
    <DataTable v-else v-model:sort="state.sort" :rows="list" :columns="columns" :row-key="(c) => String(c.id)" clickable empty-title="No clusters yet" empty-icon="globe" @row="openEdit">
      <template #verify_tls="{ row }"><span class="chip">{{ row.url.startsWith("https") ? (row.verify_tls ? "HTTPS, verified" : "HTTPS, self-signed OK") : "HTTP" }}</span></template>
      <template #created_at="{ row }">{{ fmt.dateTime(row.created_at) }}</template>
      <template #_a="{ row }"><button class="btn ghost sm" style="color:var(--critical)" @click="remove(row)">Remove</button></template>
    </DataTable>
  </div></section>
  <Modal :open="edit.open" :title="edit.id ? `Edit ${edit.name}` : 'Add cluster'" @close="edit.open = false">
    <div class="form-row"><label class="lbl">Agent URL</label><input v-model="edit.url" class="input" style="font-family:var(--mono)" placeholder="https://10.0.0.5:30800" autocomplete="off"></div>
    <div class="form-row"><label class="lbl">API token {{ edit.id ? "(leave empty to keep)" : "" }}</label><input v-model="edit.token" class="input" type="password" autocomplete="off" style="font-family:var(--mono)">
      <span class="hint">kubectl -n k10y get secret k10y-agent -o jsonpath='{.data.api-token}' | base64 -d</span></div>
    <div class="form-row"><label class="lbl">Display name</label><input v-model="edit.name" class="input" placeholder="defaults to the agent's cluster name"></div>
    <label class="pill" style="align-self:flex-start"><input v-model="edit.verify_tls" type="checkbox"> Verify the agent's TLS certificate (off for self-signed)</label>
    <div v-if="edit.test" class="test-result show" :class="edit.test.ok ? 'ok' : 'err'"><Icon :name="edit.test.ok ? 'ok' : 'crit'" /><span>{{ edit.test.text }}</span></div>
    <template #actions><button class="btn ghost" type="button" @click="edit.open = false">Cancel</button><span style="flex:1" />
      <button class="btn" :disabled="edit.busy" @click="test">Test connection</button>
      <button class="btn primary" :disabled="edit.busy || !edit.url || (!edit.id && !edit.token)" @click="save">{{ edit.id ? "Save" : "Add cluster" }}</button></template>
  </Modal>
</template>
