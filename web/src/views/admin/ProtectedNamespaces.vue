<script setup lang="ts">
// Protected namespaces per cluster: where the agent refuses every action, edit and shell. Kept by each agent (in
// its own Secret), so the choice survives agent restarts and upgrades. Needs namespaces.protect on that cluster.
import { computed, onMounted, reactive } from "vue";
import { agent } from "@/api/client";
import Badge from "@/components/Badge.vue";
import Icon from "@/components/Icon.vue";
import { useCluster } from "@/stores/cluster";
import { useSession } from "@/stores/session";
import { toast } from "@/stores/ui";

interface Row { id: number; name: string; saved: string[]; draft: string[]; source: string; persisted: boolean; known: string[];
  add: string; busy: boolean; error: string; old: boolean }
const s = useSession(), cl = useCluster();
const rows = reactive<Row[]>([]);
const mine = computed(() => s.clusters.filter((c) => s.can("namespaces.protect", c.id)));
// Namespaces whose protection keeps the cluster and k10y itself safe; removing them gets a warning.
const CORE = ["kube-system", "k10y"];

async function load() {
  rows.splice(0, rows.length, ...mine.value.map((c) => ({ id: c.id, name: c.name, saved: [], draft: [], source: "", persisted: true, known: [],
    add: "", busy: true, error: "", old: false })));
  await Promise.all(rows.map(async (r) => {
    try {
      const info = await agent(r.id).get<any>("info");
      Object.assign(r, { saved: [...info.protected_namespaces], draft: [...info.protected_namespaces], source: info.protected_source || "env",
        persisted: info.protected_persisted !== false, old: info.protected_source === undefined });
      try { r.known = await agent(r.id).get<string[]>("namespaces"); } catch { r.known = []; }
    } catch (e: any) { r.error = e.message; } finally { r.busy = false; }
  }));
}
onMounted(load);

const changed = (r: Row) => [...r.draft].sort().join() !== [...r.saved].sort().join();
const removedCore = (r: Row) => CORE.filter((n) => r.saved.includes(n) && !r.draft.includes(n));
const unknown = (r: Row) => r.known.length ? r.draft.filter((n) => !r.known.includes(n)) : [];
const suggestions = (r: Row) => r.known.filter((n) => !r.draft.includes(n));
function add(r: Row) {
  for (const n of r.add.split(/[\s,]+/).map((x) => x.trim().toLowerCase()).filter(Boolean)) if (!r.draft.includes(n)) r.draft.push(n);
  r.add = "";
}
async function save(r: Row) {
  const lost = removedCore(r);
  if (lost.length && !confirm(`Stop protecting ${lost.join(" and ")} on ${r.name}? People with the right permissions could then change or delete `
    + `what runs there${lost.includes("k10y") ? ", including the k10y agent itself" : ""}.`)) return;
  r.busy = true; r.error = "";
  try {
    const info = await agent(r.id).put<any>("settings/protected-namespaces", { namespaces: r.draft });
    Object.assign(r, { saved: [...info.protected_namespaces], draft: [...info.protected_namespaces], source: info.protected_source });
    if (cl.info && s.current?.id === r.id) cl.info.protected_namespaces = [...info.protected_namespaces];
    toast(true, `Protected namespaces saved for ${r.name}`);
  } catch (e: any) {
    r.error = /404|405|not found|method not allowed/i.test(e.message) ? "This agent is too old to change protected namespaces here. Update it first." : e.message;
  } finally { r.busy = false; }
}
</script>

<template>
  <div class="section-bar"><h2>Protected namespaces</h2>
    <span class="hint">The agent refuses every action, edit, shell and Apply YAML in these namespaces, for every user. Everything there can still be viewed. Each cluster has its own list.</span></div>
  <section class="card flush"><div class="card-body">
    <div v-if="!rows.length" class="empty"><Icon name="lock" /><div class="title">No clusters</div></div>
    <div v-for="r in rows" :key="r.id" class="pn-row">
      <div class="pn-head"><b>{{ r.name }}</b>
        <Badge v-if="r.source" :level="r.source === 'hub' ? 'good' : 'neutral'" :label="r.source === 'hub' ? 'Set in k10y' : 'Install default'" />
        <span class="grow" />
        <button v-if="changed(r)" class="btn ghost sm" type="button" :disabled="r.busy" @click="r.draft = [...r.saved]">Undo</button>
        <button class="btn primary sm" type="button" :disabled="r.busy || !changed(r) || r.old" @click="save(r)">Save</button></div>
      <div v-if="r.error" class="test-result show err"><Icon name="crit" /><span>{{ r.error }}</span></div>
      <p v-else-if="r.busy && !r.source" class="hint">Loading…</p>
      <template v-else-if="!r.error">
        <div class="pn-chips">
          <span v-for="n in r.draft" :key="n" class="chip pn-chip" :class="{ added: !r.saved.includes(n) }"><Icon name="lock" />{{ n }}
            <button type="button" class="pn-x" :aria-label="`Stop protecting ${n}`" :disabled="r.busy" @click="r.draft = r.draft.filter((x) => x !== n)">×</button></span>
          <span v-if="!r.draft.length" class="hint">Nothing is protected: every namespace can be changed.</span>
          <form class="pn-add" @submit.prevent="add(r)">
            <input v-model="r.add" class="input sm" :list="`pn-${r.id}`" placeholder="Add a namespace" aria-label="Namespace to protect" :disabled="r.busy || r.old">
            <datalist :id="`pn-${r.id}`"><option v-for="n in suggestions(r)" :key="n" :value="n" /></datalist>
            <button class="btn sm" type="submit" :disabled="!r.add.trim()">Add</button></form>
        </div>
        <p v-if="removedCore(r).length" class="pn-warn"><Icon name="warn" /> {{ removedCore(r).join(" and ") }} will no longer be protected<template v-if="removedCore(r).includes('k10y')">: the k10y agent itself could then be changed or deleted</template>.</p>
        <p v-if="unknown(r).length" class="hint">Not in the cluster (yet): {{ unknown(r).join(", ") }}. It is protected as soon as it's created.</p>
        <p v-if="r.old" class="hint">Update this agent to change its protected namespaces here.</p>
        <p v-else-if="!r.persisted" class="hint">This agent runs outside a cluster: changes last until it restarts.</p>
      </template>
    </div>
  </div></section>
  <p class="hint" style="margin-top:8px">Needs the <b>namespaces.protect</b> permission on that cluster (Admin and Kubernetes admin have it). Every change is in the activity log.</p>
</template>

<style scoped>
.pn-row { padding: 14px 16px; border-bottom: 1px solid var(--border); display: flex; flex-direction: column; gap: 10px; }
.pn-row:last-child { border-bottom: 0; }
.pn-head { display: flex; align-items: center; gap: 10px; }
.pn-chips { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.pn-chip { display: inline-flex; align-items: center; gap: 4px; font-family: var(--mono); }
.pn-chip.added { outline: 1px dashed var(--accent, currentColor); }
.pn-x { border: 0; background: none; color: var(--muted); cursor: pointer; font-size: 15px; line-height: 1; padding: 0 0 0 2px; }
.pn-x:hover { color: var(--critical); }
.pn-add { display: inline-flex; gap: 6px; margin-left: 4px; }
.pn-add .input.sm { height: 30px; width: 180px; padding: 0 8px; font-size: 13px; }
.pn-warn { margin: 0; color: var(--warning); font-size: 13px; display: flex; gap: 6px; align-items: center; }
.pn-row .hint { margin: 0; }
</style>
