<script setup lang="ts">
// Personal API tokens (scripts, CI, the k10y CLI): create one (shown once), see when each was last used, revoke.
// With `userId`, an admin's view of someone else's tokens (revoke only).
import { onMounted, reactive, ref } from "vue";
import { hub } from "@/api/client";
import { fmt } from "@/lib/format";
import { toast } from "@/stores/ui";
import Icon from "./Icon.vue";

const props = defineProps<{ userId?: number }>();
const tokens = ref<any[] | null>(null), roles = ref<any[]>([]);
const base = () => (props.userId ? `/users/${props.userId}/tokens` : "/auth/tokens");
async function load() { try { tokens.value = await hub.get(base()); } catch { tokens.value = []; } }
onMounted(async () => { await load(); if (!props.userId) try { roles.value = await hub.get("/roles"); } catch { roles.value = []; } });

const f = reactive({ open: false, name: "", expires: 90, role: null as number | null, busy: false, err: "", made: "" });
function start() { Object.assign(f, { open: true, name: "", expires: 90, role: null, busy: false, err: "", made: "" }); }
async function create() {
  f.busy = true; f.err = "";
  try { f.made = (await hub.post<any>("/auth/tokens", { name: f.name.trim(), expires_days: f.expires, role_id: f.role })).token; await load(); }
  catch (e: any) { f.err = e.message; } finally { f.busy = false; }
}
async function copy() { try { await navigator.clipboard.writeText(f.made); toast(true, "Copied"); } catch { toast(false, "Copy failed"); } }
async function revoke(t: any) {
  if (!confirm(`Revoke token "${t.name}"? Anything using it stops working right away.`)) return;
  try { await hub.del(`${base()}/${t.id}`); await load(); toast(true, `Revoked ${t.name}`); } catch (e: any) { toast(false, e.message); }
}
const EXPIRY: [number, string][] = [[30, "30 days"], [90, "90 days"], [365, "1 year"], [0, "Never"]];
</script>

<template>
  <div class="tk">
    <div v-if="tokens && !tokens.length && !f.open" class="hint">No tokens{{ userId ? "" : " yet. Use one for scripts, CI pipelines and the k10y command-line tool, instead of your password" }}.</div>
    <table v-if="tokens?.length" class="data"><thead><tr><th>Name</th><th>Token</th><th>Limited to</th><th>Last used</th><th>Expires</th><th class="actions"><span class="sr-only">Actions</span></th></tr></thead>
      <tbody><tr v-for="t in tokens" :key="t.id">
        <td class="primary">{{ t.name }}</td><td class="mono dim">{{ t.prefix }}…</td><td>{{ t.role || "All your permissions" }}</td>
        <td class="dim">{{ t.last_used_at ? `${fmt.ago(t.last_used_at)}${t.last_ip ? ` · ${t.last_ip}` : ""}` : "Never" }}</td>
        <td class="dim">{{ t.expires_at ? fmt.dateTime(t.expires_at) : "Never" }}</td>
        <td class="actions"><button class="btn ghost sm" style="color:var(--critical)" type="button" @click="revoke(t)">Revoke</button></td></tr></tbody></table>

    <template v-if="!userId">
      <button v-if="!f.open" class="btn sm" type="button" style="align-self:flex-start" @click="start">New token</button>
      <form v-else-if="!f.made" class="tk-box" @submit.prevent="create">
        <div class="form-row"><label class="lbl" for="tk-n">Name</label><input id="tk-n" v-model="f.name" class="input" maxlength="64" placeholder="e.g. GitLab deploy job" autofocus required></div>
        <div class="form-row"><span class="lbl">Expires</span><div class="segmented" role="group">
          <button v-for="[d, l] in EXPIRY" :key="d" type="button" :aria-pressed="f.expires === d" @click="f.expires = d">{{ l }}</button></div></div>
        <div class="form-row"><label class="lbl" for="tk-r">Limit to a role (optional)</label>
          <select id="tk-r" v-model="f.role" class="select"><option :value="null">All my permissions</option><option v-for="r in roles" :key="r.id" :value="r.id">{{ r.name }}</option></select>
          <span class="hint">The token can do only what both you and this role can. A read-only role makes a read-only token.</span></div>
        <div v-if="f.err" class="test-result show err"><Icon name="crit" /><span>{{ f.err }}</span></div>
        <div class="tk-row"><button class="btn ghost" type="button" @click="f.open = false">Cancel</button><span class="grow" />
          <button class="btn primary" type="submit" :disabled="f.busy || !f.name.trim()">Create token</button></div>
      </form>
      <div v-else class="tk-box">
        <div class="test-result show ok"><Icon name="ok" /><span>Copy your token now. It won't be shown again.</span></div>
        <div class="tk-row"><code class="tk-token">{{ f.made }}</code><button class="btn" type="button" @click="copy"><Icon name="copy" /><span>Copy</span></button></div>
        <p class="hint" style="margin:0">Send it as <code>Authorization: Bearer &lt;token&gt;</code> to the hub's API (<code>/api/hub/…</code>). Everything it does is in the activity log under your name.</p>
        <div class="tk-row"><span class="grow" /><button class="btn primary" type="button" @click="f.open = false; f.made = ''">Done</button></div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.tk { display: flex; flex-direction: column; gap: 12px; }
.tk-box { display: flex; flex-direction: column; gap: 12px; padding: 14px; border: 1px solid var(--border); border-radius: 10px; }
.tk-row { display: flex; gap: 8px; align-items: center; }
.tk-token { flex: 1; font-size: 13px; padding: 8px 10px; border-radius: 8px; background: var(--surface-active); overflow-wrap: anywhere; }
</style>
