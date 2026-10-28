<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { hub } from "@/api/client";
import DataTable from "@/components/DataTable.vue";
import Icon from "@/components/Icon.vue";
import Modal from "@/components/Modal.vue";
import Skeleton from "@/components/Skeleton.vue";
import { page } from "@/stores/prefs";
import { toast } from "@/stores/ui";
const state = page("roles", { key: "name", dir: 1 });
const roles = ref<any[] | null>(null), catalog = ref<{ cluster: Record<string, string>; global: Record<string, string>; haproxy?: Record<string, string> } | null>(null);
async function load() { [roles.value, catalog.value] = await Promise.all([hub.get("/roles"), hub.get("/permissions")]); }
onMounted(load);
const edit = reactive({ open: false, id: 0, name: "", description: "", perms: [] as string[], locked: false, err: "", busy: false });
function openNew() { Object.assign(edit, { open: true, id: 0, name: "", description: "", perms: ["cluster.view"], locked: false, err: "" }); }
function openEdit(r: any) { Object.assign(edit, { open: true, id: r.id, name: r.name, description: r.description, perms: [...r.permissions], locked: r.locked, err: "" }); }
async function save() {
  edit.busy = true; edit.err = "";
  const body = { name: edit.name, description: edit.description, permissions: edit.perms };
  try { if (edit.id) await hub.patch(`/roles/${edit.id}`, body); else await hub.post("/roles", body); edit.open = false; toast(true, `Saved role ${edit.name}`); load(); }
  catch (e: any) { edit.err = e.message; } finally { edit.busy = false; }
}
async function remove(r: any) {
  if (!confirm(`Delete role ${r.name}? Users lose the permissions it gave them.`)) return;
  try { await hub.del(`/roles/${r.id}`); toast(true, `Deleted role ${r.name}`); load(); } catch (e: any) { toast(false, e.message); }
}
// Which system a role is for, from its permissions (Kubernetes, HAProxy, both, or the hub).
function systemOf(r: any): string {
  // ai.use is shared (the AI assistant of either system), so it doesn't decide the system
  const k8s = r.permissions.some((p: string) => p !== "ai.use" && catalog.value?.cluster[p]), hx = r.permissions.some((p: string) => p.startsWith("haproxy."));
  const hubP = r.permissions.some((p: string) => catalog.value?.global[p]);
  return hubP && k8s && hx ? "Everything" : k8s && hx ? "Kubernetes + HAProxy" : hx ? "HAProxy" : k8s ? "Kubernetes" : hubP ? "Hub" : "–";
}
const columns = [{ key: "name", label: "Role", cls: "primary" }, { key: "system", label: "Applies to", sort: (r: any) => systemOf(r) }, { key: "description", label: "Description", cls: "wrap" },
  { key: "permissions", label: "Permissions", num: true, sort: (r: any) => r.permissions.length }, { key: "assignments", label: "Assigned", num: true }, { key: "_a", label: "Actions", nosort: true, cls: "actions" }];
</script>
<template>
  <div class="toolbar"><span class="hint">Roles are sets of permissions, one set per system. Assign them on the Users page: on everything, on one cluster (Kubernetes roles) or on one HAProxy (HAProxy roles).</span><span class="grow" />
    <button class="btn primary" @click="openNew"><Icon name="upload" /><span>New role</span></button></div>
  <section class="card flush"><div class="card-body table-wrap">
    <Skeleton v-if="!roles" :rows="3" />
    <DataTable v-else v-model:sort="state.sort" :rows="roles" :columns="columns" :row-key="(r) => String(r.id)" clickable @row="openEdit">
      <template #name="{ row }">{{ row.name }} <span v-if="row.builtin" class="pill-tag">built-in</span> <span v-if="row.locked" class="chip"><Icon name="lock" /> locked</span></template>
      <template #system="{ row }"><span class="chip">{{ systemOf(row) }}</span></template>
      <template #permissions="{ row }">{{ row.permissions.length }}</template>
      <template #_a="{ row }"><button v-if="!row.locked" class="btn ghost sm" style="color:var(--critical)" @click="remove(row)">Delete</button></template>
    </DataTable>
  </div></section>
  <Modal :open="edit.open" :title="edit.locked ? `${edit.name} (locked)` : edit.id ? `Edit role ${edit.name}` : 'New role'" wide @close="edit.open = false">
    <div class="two-col" style="margin:0">
      <div class="form-row"><label class="lbl">Name</label><input v-model="edit.name" class="input" :disabled="edit.locked"></div>
      <div class="form-row"><label class="lbl">Description</label><input v-model="edit.description" class="input" :disabled="edit.locked"></div>
    </div>
    <template v-if="catalog">
      <div class="form-row"><span class="lbl">Cluster permissions <span class="hint">(apply on the clusters the role is assigned to)</span></span>
        <div class="perm-grid"><label v-for="(desc, p) in catalog.cluster" :key="p"><input v-model="edit.perms" type="checkbox" :value="p" :disabled="edit.locked"><span><b style="font-family:var(--mono);font-size:12px">{{ p }}</b><small>{{ desc }}</small></span></label></div></div>
      <div v-if="catalog.haproxy" class="form-row"><span class="lbl">HAProxy permissions <span class="hint">(apply on the HAProxy instance the role is assigned to, or on every HAProxy)</span></span>
        <div class="perm-grid"><label v-for="(desc, p) in catalog.haproxy" :key="p"><input v-model="edit.perms" type="checkbox" :value="p" :disabled="edit.locked"><span><b style="font-family:var(--mono);font-size:12px">{{ p }}</b><small>{{ desc }}</small></span></label></div></div>
      <div class="form-row"><span class="lbl">Hub permissions <span class="hint">(only when the role is assigned on everything)</span></span>
        <div class="perm-grid"><label v-for="(desc, p) in catalog.global" :key="p"><input v-model="edit.perms" type="checkbox" :value="p" :disabled="edit.locked"><span><b style="font-family:var(--mono);font-size:12px">{{ p }}</b><small>{{ desc }}</small></span></label></div></div>
    </template>
    <div v-if="edit.err" class="act-result show err">{{ edit.err }}</div>
    <template #actions><button class="btn ghost" type="button" @click="edit.open = false">{{ edit.locked ? "Close" : "Cancel" }}</button><span style="flex:1" />
      <button v-if="!edit.locked" class="btn primary" :disabled="edit.busy || !edit.name" @click="save">Save</button></template>
  </Modal>
</template>
