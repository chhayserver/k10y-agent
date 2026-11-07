<script setup lang="ts">
// Single sign-on (OpenID Connect) providers and their group -> role mappings. Needs settings.manage.
import { computed, onMounted, reactive, ref } from "vue";
import { hub } from "@/api/client";
import Badge from "@/components/Badge.vue";
import Icon from "@/components/Icon.vue";
import Modal from "@/components/Modal.vue";
import PasswordInput from "@/components/PasswordInput.vue";
import { useSession } from "@/stores/session";
import { toast } from "@/stores/ui";

const s = useSession();
const data = ref<{ providers: any[]; callback_url: string } | null>(null), roles = ref<any[]>([]), haproxies = ref<any[]>([]);
async function load() { try { data.value = await hub.get("/settings/sso"); } catch { data.value = null; } }
onMounted(async () => {
  await load();
  try { roles.value = await hub.get("/roles"); } catch { roles.value = []; }
  try { haproxies.value = await hub.get("/haproxy"); } catch { haproxies.value = []; }
});

interface Row { group: string; role_id: number | null; scope: string }   // scope: "" | "c:<id>" | "h:<id>"
const PRESETS: Record<string, { issuer: string; username: string; groups: string; hint: string }> = {
  google: { issuer: "https://accounts.google.com", username: "email", groups: "groups", hint: "Google doesn't send groups: map nobody, and give roles by hand." },
  entra: { issuer: "https://login.microsoftonline.com/<tenant-id>/v2.0", username: "preferred_username", groups: "groups", hint: "Entra sends group object IDs (turn on the groups claim in the app registration)." },
  keycloak: { issuer: "https://keycloak.example.com/realms/<realm>", username: "preferred_username", groups: "groups", hint: "Add a Group Membership mapper (full path off) to the client." },
};
const blank = () => ({ open: false, isNew: true, id: "", name: "", issuer: "", client_id: "", client_secret: "", scopes: "openid email profile",
  username_claim: "preferred_username", groups_claim: "groups", auto_create: true, enabled: true, secret_set: false, rows: [] as Row[], busy: false, err: "", hint: "" });
const e = reactive(blank());
function openNew(preset?: string) {
  Object.assign(e, blank(), { open: true });
  const p = preset && PRESETS[preset];
  if (p) Object.assign(e, { id: preset, name: preset === "entra" ? "Microsoft" : preset![0].toUpperCase() + preset!.slice(1), issuer: p.issuer,
    username_claim: p.username, groups_claim: p.groups, hint: p.hint });
}
function openEdit(p: any) {
  Object.assign(e, blank(), { ...p, open: true, isNew: false, client_secret: "",
    rows: (p.mappings || []).map((m: any) => ({ group: m.group, role_id: m.role_id, scope: m.cluster_id ? `c:${m.cluster_id}` : m.haproxy_id ? `h:${m.haproxy_id}` : "" })) });
}
async function save() {
  e.busy = true; e.err = "";
  const mappings = e.rows.filter((r) => r.group.trim() && r.role_id).map((r) => ({ group: r.group.trim(), role_id: r.role_id,
    cluster_id: r.scope.startsWith("c:") ? Number(r.scope.slice(2)) : null, haproxy_id: r.scope.startsWith("h:") ? Number(r.scope.slice(2)) : null }));
  try {
    await hub.put(`/settings/sso/${e.id}`, { id: e.id, name: e.name, issuer: e.issuer.trim(), client_id: e.client_id.trim(), client_secret: e.client_secret || null,
      scopes: e.scopes, username_claim: e.username_claim, groups_claim: e.groups_claim, auto_create: e.auto_create, enabled: e.enabled, mappings });
    e.open = false; toast(true, `Saved ${e.name}`); await load();
  } catch (err: any) { e.err = err.message; } finally { e.busy = false; }
}
async function remove(p: any) {
  if (!confirm(`Remove ${p.name}? People can no longer sign in with it (their k10y accounts stay).`)) return;
  try { await hub.del(`/settings/sso/${p.id}`); await load(); } catch (err: any) { toast(false, err.message); }
}
async function copy(t: string) { try { await navigator.clipboard.writeText(t); toast(true, "Copied"); } catch { toast(false, t); } }
const roleName = (id: number) => roles.value.find((r) => r.id === id)?.name || `#${id}`;
const scopeName = (m: any) => m.cluster_id ? `cluster ${s.clusters.find((c) => c.id === m.cluster_id)?.name || m.cluster_id}`
  : m.haproxy_id ? `HAProxy ${haproxies.value.find((h) => h.id === m.haproxy_id)?.name || m.haproxy_id}` : "everything";
const canSave = computed(() => /^[a-z0-9][a-z0-9-]{0,30}$/.test(e.id) && e.name && /^https?:\/\//.test(e.issuer) && e.client_id && (e.client_secret || e.secret_set));
</script>

<template>
  <div class="section-bar"><h2>Single sign-on</h2><span class="hint">Sign in with Google, Microsoft Entra ID, Keycloak, Okta or any OpenID Connect provider. Groups can give roles.</span>
    <span class="grow" /><button class="btn primary" type="button" @click="openNew()"><Icon name="key" /><span>Add provider</span></button></div>
  <section class="card flush" style="margin-bottom:28px"><div class="card-body">
    <div v-if="data && !data.providers.length" class="empty"><Icon name="key" /><div class="title">No single sign-on yet</div>
      <div class="s" style="display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin-top:10px">Start from:
        <button class="btn sm" type="button" @click="openNew('google')">Google</button><button class="btn sm" type="button" @click="openNew('entra')">Microsoft Entra ID</button>
        <button class="btn sm" type="button" @click="openNew('keycloak')">Keycloak</button></div></div>
    <table v-else-if="data" class="data"><thead><tr><th>Provider</th><th>Issuer</th><th>Groups → roles</th><th>New users</th><th>Status</th><th class="actions"><span class="sr-only">Actions</span></th></tr></thead>
      <tbody><tr v-for="p in data.providers" :key="p.id" class="clickable" @click="(ev) => !(ev.target as HTMLElement).closest('button') && openEdit(p)">
        <td class="primary">{{ p.name }}</td><td class="mono" style="max-width:280px;overflow:hidden;text-overflow:ellipsis">{{ p.issuer }}</td>
        <td><span v-if="!p.mappings?.length" class="dim">None (roles by hand)</span><div v-for="m in p.mappings" :key="m.group + m.role_id" style="font-size:13px"><code>{{ m.group }}</code> → {{ roleName(m.role_id) }} <span class="dim">on {{ scopeName(m) }}</span></div></td>
        <td>{{ p.auto_create ? "Created at first sign-in" : "Must exist" }}</td>
        <td><Badge :level="p.enabled ? 'good' : 'neutral'" :label="p.enabled ? 'On' : 'Off'" /></td>
        <td class="actions"><button class="btn ghost sm" style="color:var(--critical)" type="button" @click="remove(p)">Remove</button></td></tr></tbody></table>
    <p v-if="data" class="hint" style="margin:12px 16px">Redirect (callback) URL to register at the provider:
      <code>{{ data.callback_url }}</code> <button class="btn ghost sm" type="button" @click="copy(data.callback_url)"><Icon name="copy" /></button>
      Set <code>K10Y_PUBLIC_URL</code> on the hub if this isn't the address people use.</p>
  </div></section>

  <Modal :open="e.open" :title="e.isNew ? 'Add single sign-on' : `Edit ${e.name}`" wide @close="e.open = false">
    <p v-if="e.hint" class="hint" style="margin:0">{{ e.hint }}</p>
    <div class="two-col-form">
      <div class="form-row"><label class="lbl" for="sso-name">Button label</label><input id="sso-name" v-model="e.name" class="input" placeholder="e.g. Google"><span class="hint">Shown as "Sign in with {{ e.name || '…' }}".</span></div>
      <div class="form-row"><label class="lbl" for="sso-id">ID</label><input id="sso-id" v-model="e.id" class="input mono" :disabled="!e.isNew" placeholder="e.g. google"><span class="hint">Lowercase letters, numbers and dashes.</span></div>
    </div>
    <div class="form-row"><label class="lbl" for="sso-iss">Issuer URL</label><input id="sso-iss" v-model="e.issuer" class="input mono" placeholder="https://accounts.google.com"></div>
    <div class="two-col-form">
      <div class="form-row"><label class="lbl" for="sso-cid">Client ID</label><input id="sso-cid" v-model="e.client_id" class="input mono" autocomplete="off"></div>
      <div class="form-row"><label class="lbl">Client secret</label><PasswordInput v-model="e.client_secret" autocomplete="off" mono :placeholder="e.secret_set ? 'Saved. Leave empty to keep it' : ''" /></div>
    </div>
    <div class="two-col-form">
      <div class="form-row"><label class="lbl" for="sso-u">Username from claim</label><input id="sso-u" v-model="e.username_claim" class="input mono"></div>
      <div class="form-row"><label class="lbl" for="sso-g">Groups from claim</label><input id="sso-g" v-model="e.groups_claim" class="input mono"><span class="hint">Dotted paths work, e.g. <code>realm_access.roles</code>.</span></div>
    </div>
    <div class="form-row"><label class="lbl" for="sso-sc">Scopes</label><input id="sso-sc" v-model="e.scopes" class="input mono"></div>
    <div class="form-row"><span class="lbl">Groups → roles</span>
      <p class="hint" style="margin:0">At every sign-in, the roles from these mappings are replaced by what the person's groups give. Roles you give by hand in Users stay.</p>
      <div v-for="(r, i) in e.rows" :key="i" class="assign-row">
        <input v-model="r.group" class="input mono" placeholder="group name or ID" aria-label="Group">
        <select v-model="r.role_id" class="select" aria-label="Role"><option :value="null" disabled>Role…</option><option v-for="ro in roles" :key="ro.id" :value="ro.id">{{ ro.name }}</option></select>
        <select v-model="r.scope" class="select" aria-label="On"><option value="">on everything</option>
          <option v-for="c in s.clusters" :key="'c' + c.id" :value="`c:${c.id}`">on cluster {{ c.name }}</option>
          <option v-for="h in haproxies" :key="'h' + h.id" :value="`h:${h.id}`">on HAProxy {{ h.name }}</option></select>
        <button class="btn ghost sm" type="button" aria-label="Remove mapping" @click="e.rows.splice(i, 1)">×</button></div>
      <button class="btn sm" type="button" style="align-self:flex-start" @click="e.rows.push({ group: '', role_id: null, scope: '' })">Add mapping</button></div>
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      <label class="pill"><input v-model="e.auto_create" type="checkbox"> Create k10y accounts at first sign-in</label>
      <label class="pill"><input v-model="e.enabled" type="checkbox"> Enabled</label></div>
    <div v-if="e.err" class="test-result show err"><Icon name="crit" /><span>{{ e.err }}</span></div>
    <template #actions><button class="btn ghost" type="button" @click="e.open = false">Cancel</button><span style="flex:1" />
      <button class="btn primary" type="button" :disabled="e.busy || !canSave" @click="save">Save</button></template>
  </Modal>
</template>

<style scoped>
.two-col-form { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; }
</style>
