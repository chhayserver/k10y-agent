<script setup lang="ts">
// One search box for everything: objects in every cluster (Deployments, Pods, Services, Helm releases…) and every
// HAProxy (frontends, backends, servers) the user can see. Ctrl/⌘ K focuses it.
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { hub } from "@/api/client";
import { to } from "@/lib/nav";
import { useHaproxy } from "@/stores/haproxy";
import { useSession } from "@/stores/session";
import { openDetail } from "@/stores/ui";
import Icon from "./Icon.vue";

const s = useSession(), h = useHaproxy(), router = useRouter();
const q = ref(""), open = ref(false), busy = ref(false), results = ref<any[]>([]), errors = ref<any[]>([]), active = ref(0);
const input = ref<HTMLInputElement | null>(null);
let timer = 0, seq = 0;
watch(q, (v) => {
  clearTimeout(timer);
  if (v.trim().length < 2) { results.value = []; return; }
  timer = window.setTimeout(async () => {
    const my = ++seq; busy.value = true;
    try { const r = await hub.get<{ results: any[]; errors: any[] }>("/search", { q: v.trim() }); if (my === seq) { results.value = r.results; errors.value = r.errors; active.value = 0; } }
    catch { if (my === seq) results.value = []; } finally { if (my === seq) busy.value = false; }
  }, 250);
});
const groups = computed(() => {
  const g = new Map<string, any[]>();
  for (const x of results.value.slice(0, 60)) {
    const key = x.system === "haproxy" ? `HAProxy · ${x.instance}` : `Kubernetes · ${x.cluster}`;
    g.set(key, [...(g.get(key) || []), x]);
  }
  return [...g];
});
const flat = computed(() => groups.value.flatMap(([, xs]) => xs));
function choose(x: any) {
  open.value = false; q.value = ""; input.value?.blur();
  if (x.system === "haproxy") {
    h.select(x.haproxy_id);
    const focus = x.kind === "HAProxy frontend" ? `f:${x.name}` : x.kind === "HAProxy backend" ? `b:${x.name}` : x.kind === "HAProxy server" ? `b:${x.namespace}` : "";
    router.push({ path: to(x.kind === "HAProxy frontend" ? "haproxy-frontends" : x.kind === "HAProxy" ? "haproxy-overview" : "haproxy-backends"), query: focus ? { focus } : {} });
    return;
  }
  if (s.currentId !== x.cluster_id) s.setCluster(x.cluster_id);
  if (x.kind === "HelmRelease") { router.push(to("helm")); return; }
  openDetail(x.kind, x.namespace, x.name);
}
function onKey(e: KeyboardEvent) {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); input.value?.focus(); open.value = true; }
}
function nav(e: KeyboardEvent) {
  if (e.key === "ArrowDown") { e.preventDefault(); active.value = Math.min(flat.value.length - 1, active.value + 1); }
  else if (e.key === "ArrowUp") { e.preventDefault(); active.value = Math.max(0, active.value - 1); }
  else if (e.key === "Enter" && flat.value[active.value]) { e.preventDefault(); choose(flat.value[active.value]); }
  else if (e.key === "Escape") { open.value = false; input.value?.blur(); }
}
onMounted(() => document.addEventListener("keydown", onKey));
onBeforeUnmount(() => document.removeEventListener("keydown", onKey));
const isMac = /Mac|iPhone|iPad/.test(navigator.platform);
</script>

<template>
  <div class="gs" @focusout="(e) => !(e.currentTarget as HTMLElement).contains(e.relatedTarget as Node) && (open = false)">
    <label class="search gs-box"><Icon name="search" />
      <input ref="input" v-model="q" class="input" placeholder="Search everything" aria-label="Search all clusters and HAProxy" autocomplete="off"
        @focus="open = true" @keydown="nav"><kbd>{{ isMac ? "⌘" : "Ctrl" }} K</kbd></label>
    <div v-if="open && q.trim().length >= 2" class="gs-pop" role="listbox">
      <p v-if="busy && !results.length" class="gs-note">Searching…</p>
      <p v-else-if="!results.length" class="gs-note">Nothing named "{{ q.trim() }}"<template v-if="errors.length"> (couldn't reach {{ errors.map((e) => e.cluster).join(", ") }})</template>.</p>
      <template v-for="[g, xs] in groups" :key="g">
        <div class="gs-group">{{ g }}</div>
        <button v-for="x in xs" :key="`${g}/${x.kind}/${x.namespace}/${x.name}`" type="button" class="gs-item" role="option"
          :class="{ on: flat[active] === x }" tabindex="-1" @mousedown.prevent="choose(x)" @mouseenter="active = flat.indexOf(x)">
          <span class="gs-kind">{{ x.kind.replace("HAProxy ", "") }}</span>
          <span class="mono gs-name">{{ x.name }}</span>
          <span v-if="x.namespace" class="dim gs-ns">{{ x.namespace }}</span>
          <span class="dim gs-st">{{ x.status || "" }}</span></button>
      </template>
      <p v-if="results.length > 60" class="gs-note">Showing 60 of {{ results.length }}. Type more to narrow it down.</p>
    </div>
  </div>
</template>

<style scoped>
.gs { position: relative; }
.gs-box { min-width: 260px; }
.gs-box kbd { font-size: 11px; }
.gs-pop { position: absolute; top: calc(100% + 6px); right: 0; width: min(560px, 92vw); max-height: 70vh; overflow: auto; z-index: 60;
  background: var(--surface); border: 1px solid var(--border); border-radius: 12px; box-shadow: 0 12px 40px rgb(0 0 0 / .35); padding: 6px; }
.gs-group { font-size: 11.5px; font-weight: 600; letter-spacing: .04em; text-transform: uppercase; color: var(--muted); padding: 8px 10px 4px; }
.gs-item { display: grid; grid-template-columns: 110px minmax(0, 1fr) auto auto; gap: 10px; align-items: center; width: 100%; padding: 7px 10px; border: 0; border-radius: 8px;
  background: none; color: var(--text); font: inherit; font-size: 13px; text-align: left; cursor: pointer; }
.gs-item.on { background: var(--surface-active); }
.gs-kind { color: var(--muted); font-size: 12px; }
.gs-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gs-ns, .gs-st { font-size: 12px; white-space: nowrap; }
.gs-note { margin: 8px 10px; font-size: 13px; color: var(--muted); }
@media (max-width: 900px) { .gs-box { min-width: 160px; } .gs-box kbd { display: none; } }
</style>
