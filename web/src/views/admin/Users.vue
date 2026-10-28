<script setup lang="ts">
import PasswordInput from "@/components/PasswordInput.vue";
import { computed, onMounted, reactive, ref } from "vue";
import { hub } from "@/api/client";
import Badge from "@/components/Badge.vue";
import DataTable from "@/components/DataTable.vue";
import Icon from "@/components/Icon.vue";
import Modal from "@/components/Modal.vue";
import SessionsList from "@/components/SessionsList.vue";
import Skeleton from "@/components/Skeleton.vue";
import { fmt } from "@/lib/format";
import { page } from "@/stores/prefs";
import { useSession } from "@/stores/session";
import { toast } from "@/stores/ui";

const s = useSession(), state = page("users", { key: "username", dir: 1 });
const users = ref<any[] | null>(null), roles = ref<any[]>([]), clusters = ref<any[]>([]), haproxies = ref<any[]>([]), error = ref("");
async function load() {
  try { [users.value, roles.value, clusters.value] = await Promise.all([hub.get("/users"), hub.get("/roles"), hub.get("/clusters")]);
    haproxies.value = await hub.get<any[]>("/haproxy").catch(() => []); }
  catch (e: any) { error.value = e.message; }
}
onMounted(load);
const roleName = (id: number) => roles.value.find((r) => r.id === id)?.name || "?";
const clusterName = (id: number | null) => (id == null ? "all clusters" : clusters.value.find((c) => c.id === id)?.name || "?");
// Where a role applies: everything, one cluster, or one HAProxy instance ("all" | "c:3" | "h:2").
type Assign = { role_id: number; cluster_id: number | null; haproxy_id?: number | null };
const scopeOf = (a: Assign) => (a.haproxy_id ? `h:${a.haproxy_id}` : a.cluster_id ? `c:${a.cluster_id}` : "all");
function setScope(a: Assign, v: string) {
  a.cluster_id = v.startsWith("c:") ? Number(v.slice(2)) : null;
  a.haproxy_id = v.startsWith("h:") ? Number(v.slice(2)) : null;
}
const scopeName = (a: Assign) => (a.haproxy_id ? `HAProxy ${haproxies.value.find((x) => x.id === a.haproxy_id)?.name || "?"}`
  : a.cluster_id ? clusterName(a.cluster_id) : "everything");
const rows = computed(() => { const q = state.q.trim().toLowerCase(); return (users.value || []).filter((u) => !q || `${u.username} ${u.display_name}`.toLowerCase().includes(q)); });

const edit = reactive({ open: false, id: 0, username: "", display_name: "", password: "", is_active: true, must_change_password: true, assignments: [] as Assign[], err: "", busy: false });
function openNew() { Object.assign(edit, { open: true, id: 0, username: "", display_name: "", password: "", is_active: true, must_change_password: true, assignments: [{ role_id: roles.value.find((r) => r.name === "Kubernetes viewer")?.id || roles.value[0]?.id, cluster_id: null }], err: "" }); }
const userSessions = ref<any[] | null>(null);
async function loadUserSessions(id: number) { userSessions.value = null; try { userSessions.value = await hub.get(`/users/${id}/sessions`); } catch { userSessions.value = []; } }
async function signOutEverywhere(u: { id: number; username: string }) {
  if (!confirm(`Sign ${u.username} out of every device?`)) return;
  try { const r = await hub.post(`/users/${u.id}/sessions/revoke`); toast(true, `Signed ${u.username} out of ${r.revoked} session(s)`); load(); if (edit.open) loadUserSessions(u.id); }
  catch (e: any) { toast(false, e.message); }
}
async function setActive(u: any, active: boolean) {
  if (!active && !confirm(`Disable ${u.username}? They are signed out now and cannot sign in until enabled again.`)) return;
  try { await hub.patch(`/users/${u.id}`, { is_active: active }); toast(true, `${u.username} ${active ? "enabled" : "disabled"}`); load(); }
  catch (e: any) { toast(false, e.message); }
}
function openEdit(u: any) { loadUserSessions(u.id); Object.assign(edit, { open: true, id: u.id, username: u.username, display_name: u.display_name, password: "", is_active: u.is_active, must_change_password: u.must_change_password, assignments: u.assignments.map((a: any) => ({ role_id: a.role_id, cluster_id: a.cluster_id, haproxy_id: a.haproxy_id || null })), err: "" }); }
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
  { key: "assignments", label: "Roles", cls: "wrap", nosort: true }, { key: "last_seen_at", label: "Last active", sort: (u: any) => u.last_seen_at || u.last_login_at || "" },
  { key: "sessions", label: "Sessions", num: true }, { key: "_a", label: "Actions", nosort: true, cls: "actions" }];
</script>
<template>
  <div class="toolbar"><label class="search"><Icon name="search" /><input v-model="state.q" class="input" placeholder="Search users" aria-label="Search"></label>
    <span class="grow" /><button class="btn primary" @click="openNew"><Icon name="upload" /><span>New user</span></button></div>
  <div v-if="error" class="banner show">{{ error }}</div>
  <section class="card flush"><div class="card-body table-wrap">
    <Skeleton v-if="!users && !error" :rows="4" />
    <DataTable v-else-if="users" v-model:sort="state.sort" :rows="rows" :columns="columns" :row-key="(u) => String(u.id)" clickable empty-title="No users" @row="openEdit">
      <template #is_active="{ row }"><Badge :level="row.is_active ? 'good' : 'neutral'" :label="row.is_active ? 'Active' : 'Disabled'" /> <span v-if="row.must_change_password" class="pill-tag">must change password</span></template>
      <template #assignments="{ row }"><span v-for="(a, i) in row.assignments" :key="i" class="chip">{{ a.role }} · {{ scopeName(a) }}</span><span v-if="!row.assignments.length" class="dim">no access</span></template>
      <template #last_seen_at="{ row }"><span v-if="row.last_seen_at">{{ fmt.ago(row.last_seen_at) }}</span>
        <span v-else class="dim">{{ row.last_login_at ? `last login ${fmt.ago(row.last_login_at)}` : "never signed in" }}</span></template>
      <template #sessions="{ row }"><span :class="{ dim: !row.sessions }">{{ row.sessions || 0 }}</span></template>
      <template #_a="{ row }"><template v-if="row.id !== s.me?.user.id">
        <button v-if="row.sessions" class="btn ghost sm" @click="signOutEverywhere(row)">Sign out</button>
        <button class="btn ghost sm" @click="setActive(row, !row.is_active)">{{ row.is_active ? "Disable" : "Enable" }}</button>
        <button class="btn ghost sm" style="color:var(--critical)" @click="remove(row)">Delete</button></template></template>
    </DataTable>
  </div></section>

  <Modal :open="edit.open" :title="edit.id ? `Edit ${edit.username}` : 'New user'" wide @close="edit.open = false">
    <div class="two-col" style="margin:0">
      <div class="form-row"><label class="lbl">Username</label><input v-model="edit.username" class="input" :disabled="!!edit.id" autocomplete="off"></div>
      <div class="form-row"><label class="lbl">Display name</label><input v-model="edit.display_name" class="input" autocomplete="off"></div>
      <div class="form-row"><label class="lbl">{{ edit.id ? "Reset password (optional)" : "Password" }}</label><PasswordInput v-model="edit.password" autocomplete="new-password" placeholder="at least 8 characters, letters + numbers" /></div>
      <div class="form-row" style="justify-content:flex-end"><label class="pill"><input v-model="edit.must_change_password" type="checkbox"> Must change password at next login</label>
        <label class="pill"><input v-model="edit.is_active" type="checkbox"> Account active</label></div>
    </div>
    <div class="form-row"><span class="lbl">Roles</span>
      <p class="hint" style="margin:0">A role on <b>everything</b> applies to every cluster and every HAProxy, and also grants its hub permissions (users, clusters, settings, alerts). On <b>one cluster</b>, only its cluster permissions count there; on <b>one HAProxy</b>, only its HAProxy permissions count there.</p>
      <div v-for="(a, i) in edit.assignments" :key="i" class="assign-row">
        <select v-model="a.role_id" class="select"><option v-for="r in roles" :key="r.id" :value="r.id">{{ r.name }}</option></select>
        <span class="hint">on</span>
        <select :value="scopeOf(a)" class="select" aria-label="Where the role applies" @change="setScope(a, ($event.target as HTMLSelectElement).value)">
          <option value="all">Everything</option>
          <optgroup v-if="clusters.length" label="One cluster"><option v-for="c in clusters" :key="c.id" :value="`c:${c.id}`">{{ c.name }}</option></optgroup>
          <optgroup v-if="haproxies.length" label="One HAProxy"><option v-for="x in haproxies" :key="x.id" :value="`h:${x.id}`">{{ x.name }}</option></optgroup></select>
        <button class="btn icon-only ghost" aria-label="Remove role" @click="edit.assignments.splice(i, 1)"><Icon name="x" /></button>
      </div>
      <button class="btn sm" style="align-self:flex-start" @click="edit.assignments.push({ role_id: roles[0]?.id, cluster_id: null, haproxy_id: null })">Add role</button>
    </div>
    <div v-if="edit.id" class="form-row"><div style="display:flex;align-items:center;gap:8px"><span class="lbl" style="flex:1">Signed-in devices</span>
      <button v-if="userSessions?.length" class="btn sm" type="button" @click="signOutEverywhere({ id: edit.id, username: edit.username })">Sign out everywhere</button></div>
      <SessionsList :sessions="userSessions" /></div>
    <div v-if="edit.err" class="act-result show err">{{ edit.err }}</div>
    <template #actions><button class="btn ghost" type="button" @click="edit.open = false">Cancel</button><span style="flex:1" />
      <button class="btn primary" :disabled="edit.busy || !edit.username || (!edit.id && !edit.password)" @click="save">Save</button></template>
  </Modal>
</template>
