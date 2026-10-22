<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { hub } from "@/api/client";
import Badge from "@/components/Badge.vue";
import DataTable from "@/components/DataTable.vue";
import Icon from "@/components/Icon.vue";
import Modal from "@/components/Modal.vue";
import Skeleton from "@/components/Skeleton.vue";
import { fmt } from "@/lib/format";
import { page } from "@/stores/prefs";
import { useSession } from "@/stores/session";
import { toast } from "@/stores/ui";

const s = useSession(), state = page("users", { key: "username", dir: 1 });
const users = ref<any[] | null>(null), roles = ref<any[]>([]), clusters = ref<any[]>([]), error = ref("");
async function load() {
  try { [users.value, roles.value, clusters.value] = await Promise.all([hub.get("/users"), hub.get("/roles"), hub.get("/clusters")]); }
  catch (e: any) { error.value = e.message; }
}
onMounted(load);
const roleName = (id: number) => roles.value.find((r) => r.id === id)?.name || "?";
const clusterName = (id: number | null) => (id == null ? "all clusters" : clusters.value.find((c) => c.id === id)?.name || "?");
const rows = computed(() => { const q = state.q.trim().toLowerCase(); return (users.value || []).filter((u) => !q || `${u.username} ${u.display_name}`.toLowerCase().includes(q)); });

const edit = reactive({ open: false, id: 0, username: "", display_name: "", password: "", is_active: true, must_change_password: true, assignments: [] as { role_id: number; cluster_id: number | null }[], err: "", busy: false });
function openNew() { Object.assign(edit, { open: true, id: 0, username: "", display_name: "", password: "", is_active: true, must_change_password: true, assignments: [{ role_id: roles.value.find((r) => r.name === "Viewer")?.id || roles.value[0]?.id, cluster_id: null }], err: "" }); }
function openEdit(u: any) { Object.assign(edit, { open: true, id: u.id, username: u.username, display_name: u.display_name, password: "", is_active: u.is_active, must_change_password: u.must_change_password, assignments: u.assignments.map((a: any) => ({ role_id: a.role_id, cluster_id: a.cluster_id })), err: "" }); }
async function save() {
  edit.err = ""; edit.busy = true;
  const body: any = { display_name: edit.display_name, is_active: edit.is_active, assignments: edit.assignments.filter((a) => a.role_id) };
  if (edit.password) { body.password = edit.password; body.must_change_password = edit.must_change_password; }
  try {
    if (edit.id) await hub.patch(`/users/${edit.id}`, body);
    else await hub.post("/users", { ...body, username: edit.username, password: edit.password, must_change_password: edit.must_change_password });
    edit.open = false; toast(true, `Saved ${edit.username}`); load();
  } catch (e: any) { edit.err = e.message; } finally { edit.busy = false; }
}
async function remove(u: any) {
  if (!confirm(`Delete user ${u.username}? This cannot be undone.`)) return;
  try { await hub.del(`/users/${u.id}`); toast(true, `Deleted ${u.username}`); load(); } catch (e: any) { toast(false, e.message); }
}
const columns = [{ key: "username", label: "Username", cls: "primary" }, { key: "display_name", label: "Name" }, { key: "is_active", label: "Status" },
  { key: "assignments", label: "Roles", cls: "wrap", nosort: true }, { key: "last_login_at", label: "Last login" }, { key: "_a", label: "Actions", nosort: true, cls: "actions" }];
</script>
<template>
  <div class="toolbar"><label class="search"><Icon name="search" /><input v-model="state.q" class="input" placeholder="Search users" aria-label="Search"></label>
    <span class="grow" /><button class="btn primary" @click="openNew"><Icon name="upload" /><span>New user</span></button></div>
  <div v-if="error" class="banner show">{{ error }}</div>
  <section class="card flush"><div class="card-body table-wrap">
    <Skeleton v-if="!users && !error" :rows="4" />
    <DataTable v-else-if="users" v-model:sort="state.sort" :rows="rows" :columns="columns" :row-key="(u) => String(u.id)" clickable empty-title="No users" @row="openEdit">
      <template #is_active="{ row }"><Badge :level="row.is_active ? 'good' : 'neutral'" :label="row.is_active ? 'Active' : 'Disabled'" /> <span v-if="row.must_change_password" class="pill-tag">must change password</span></template>
      <template #assignments="{ row }"><span v-for="(a, i) in row.assignments" :key="i" class="chip">{{ a.role }} · {{ clusterName(a.cluster_id) }}</span><span v-if="!row.assignments.length" class="dim">no access</span></template>
      <template #last_login_at="{ row }">{{ row.last_login_at ? fmt.dateTime(row.last_login_at) : "never" }}</template>
      <template #_a="{ row }"><button v-if="row.id !== s.me?.user.id" class="btn ghost sm" style="color:var(--critical)" @click="remove(row)">Delete</button></template>
    </DataTable>
  </div></section>

  <Modal :open="edit.open" :title="edit.id ? `Edit ${edit.username}` : 'New user'" wide @close="edit.open = false">
    <div class="two-col" style="margin:0">
      <div class="form-row"><label class="lbl">Username</label><input v-model="edit.username" class="input" :disabled="!!edit.id" autocomplete="off"></div>
      <div class="form-row"><label class="lbl">Display name</label><input v-model="edit.display_name" class="input" autocomplete="off"></div>
      <div class="form-row"><label class="lbl">{{ edit.id ? "Reset password (optional)" : "Password" }}</label><input v-model="edit.password" class="input" type="password" autocomplete="new-password" placeholder="at least 8 characters, letters + numbers"></div>
      <div class="form-row" style="justify-content:flex-end"><label class="pill"><input v-model="edit.must_change_password" type="checkbox"> Must change password at next login</label>
        <label class="pill"><input v-model="edit.is_active" type="checkbox"> Account active</label></div>
    </div>
    <div class="form-row"><span class="lbl">Roles</span>
      <p class="hint" style="margin:0">A role on <b>all clusters</b> also grants its global permissions (users, clusters, settings, alerts). A role on one cluster applies only there.</p>
      <div v-for="(a, i) in edit.assignments" :key="i" class="assign-row">
        <select v-model="a.role_id" class="select"><option v-for="r in roles" :key="r.id" :value="r.id">{{ r.name }}</option></select>
        <span class="hint">on</span>
        <select v-model="a.cluster_id" class="select"><option :value="null">All clusters</option><option v-for="c in clusters" :key="c.id" :value="c.id">{{ c.name }}</option></select>
        <button class="btn icon-only ghost" aria-label="Remove role" @click="edit.assignments.splice(i, 1)"><Icon name="x" /></button>
      </div>
      <button class="btn sm" style="align-self:flex-start" @click="edit.assignments.push({ role_id: roles[0]?.id, cluster_id: null })">Add role</button>
    </div>
    <div v-if="edit.err" class="act-result show err">{{ edit.err }}</div>
    <template #actions><button class="btn ghost" type="button" @click="edit.open = false">Cancel</button><span style="flex:1" />
      <button class="btn primary" :disabled="edit.busy || !edit.username || (!edit.id && !edit.password)" @click="save">Save</button></template>
  </Modal>
</template>
