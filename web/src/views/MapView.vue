<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { agent } from "@/api/client";
import EmptyState from "@/components/EmptyState.vue";
import Icon from "@/components/Icon.vue";
import Skeleton from "@/components/Skeleton.vue";
import { DETAIL_KINDS, KIND_ICON } from "@/lib/format";
import { layoutMap, MAP, relatedSet, type Topology } from "@/lib/maplayout";
import { useCluster } from "@/stores/cluster";
import { prefs } from "@/stores/prefs";
import { useSession } from "@/stores/session";
import { openDetail } from "@/stores/ui";

const s = useSession(), c = useCluster(), route = useRoute();
const namespaces = ref<string[]>([]), topo = ref<Topology | null>(null), error = ref("");
const selected = ref<string | null>((route.query.select as string) || null), hover = ref<string | null>(null);
let sig = "";
async function load() {
  const a = agent(s.current!.id);
  try {
    namespaces.value = await a.get("namespaces");
    if (!prefs.map.ns || !namespaces.value.includes(prefs.map.ns)) {
      const prot = c.info?.protected_namespaces || [];
      prefs.map.ns = (c.data?.deployments || []).map((d: any) => d.namespace).filter((n: string) => !prot.includes(n)).sort()[0] || "default";
    }
    const t = await a.get<Topology>("topology", { namespace: prefs.map.ns });
    const next = JSON.stringify(t);
    if (next !== sig) { sig = next; topo.value = t; if (selected.value && !t.nodes.some((n) => n.id === selected.value)) selected.value = null; }
    error.value = "";
  } catch (e: any) { error.value = e.message; }
}
onMounted(load);
watch(() => c.lastUpdated, load);
watch(() => prefs.map.ns, () => { selected.value = null; sig = ""; topo.value = null; load(); });

const L = computed(() => (topo.value && topo.value.nodes.length ? layoutMap(topo.value, prefs.map) : null));
const focus = computed(() => selected.value || hover.value);
const on = computed<Set<string> | null>(() => {
  if (!topo.value || !L.value) return null;
  if (focus.value && L.value.byId[focus.value]) return relatedSet(topo.value, focus.value);
  const q = prefs.map.q.trim().toLowerCase();
  return q ? new Set(topo.value.nodes.filter((n) => `${n.name} ${n.kind}`.toLowerCase().includes(q)).map((n) => n.id)) : null;
});
const sel = computed(() => topo.value?.nodes.find((n) => n.id === selected.value) || null);
const REL_IN: Record<string, string> = { routes: "Routed from ingress", selects: "Selected by service", "selects-workload": "Exposed by service", owns: "Owned by", "rs-owns": "Owned by", "rs-pod": "Created by ReplicaSet", uses: "Used by" };
const REL_OUT: Record<string, string> = { routes: "Routes to service", selects: "Sends traffic to pods", owns: "Pods", "rs-owns": "ReplicaSets", "rs-pod": "Pods", uses: "Uses" };
const relations = computed(() => {
  const t = topo.value, id = selected.value, g: Record<string, Map<string, string[]>> = {};
  if (!t || !id) return g;
  const byId = Object.fromEntries(t.nodes.map((n) => [n.id, n]));
  const add = (label: string, other: string, how: string[]) => { if (!byId[other]) return; const m = (g[label] ||= new Map()); m.set(other, [...new Set([...(m.get(other) || []), ...how])]); };
  for (const e of t.edges) {
    if (e.to === id && !(sel.value?.kind === "Pod" && e.type === "owns" && t.edges.some((x) => x.type === "rs-pod" && x.to === id))) add(REL_IN[e.type], e.from, e.labels);
    if (e.from === id && e.type !== "selects-workload") add(REL_OUT[e.type], e.to, e.labels);
  }
  return g;
});
const nodeOf = (id: string) => topo.value!.nodes.find((n) => n.id === id)!;
function goto(id: string) {
  if (L.value && !L.value.byId[id]) { if (id.startsWith("ReplicaSet/")) prefs.map.rs = true; else prefs.map.config = true; }
  selected.value = id;
}
function openSel() { const n = sel.value!; openDetail(n.kind, n.namespace, n.name); }
</script>

<template>
  <div class="toolbar">
    <select v-model="prefs.map.ns" class="select" aria-label="Namespace"><option v-for="n in namespaces" :key="n" :value="n">{{ n }}{{ c.isProtected(n) ? " (protected)" : "" }}</option></select>
    <label class="search"><Icon name="search" /><input v-model="prefs.map.q" class="input" placeholder="Find on map" aria-label="Find on map"><kbd>/</kbd></label>
    <label class="pill"><input v-model="prefs.map.rs" type="checkbox"> ReplicaSets</label>
    <label class="pill"><input v-model="prefs.map.config" type="checkbox"> Config &amp; storage</label>
    <span class="grow" />
    <div class="map-legend" aria-hidden="true">
      <span><svg width="26" height="8"><path d="M1 4h24" stroke="var(--axis)" stroke-width="1.5" /></svg>routes / owns</span>
      <span><svg width="26" height="8"><path d="M1 4h24" stroke="var(--axis)" stroke-width="1.5" stroke-dasharray="4 4" /></svg>uses config</span>
    </div>
  </div>
  <div class="map-layout">
    <section class="card"><div class="map-scroll">
      <EmptyState v-if="error && !topo" title="Could not load the map" :sub="error" icon="crit" />
      <Skeleton v-else-if="!topo" :rows="6" />
      <EmptyState v-else-if="!L" title="Nothing to map" :sub="`Namespace ${prefs.map.ns} has no workloads, pods or services.`" icon="map" />
      <div v-else class="map-canvas" :class="{ focus: on }" :style="{ width: `${L.width}px`, height: `${L.height}px` }" @click.self="selected = null" @mouseleave="hover = null">
        <svg class="edges" :width="L.width" :height="L.height">
          <defs><marker id="map-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L8,4 L0,8 z" fill="var(--axis)" /></marker></defs>
          <path v-for="(e, i) in L.edges" :key="i" class="edge" :class="[e.type, { on: on && focus && on.has(e.from) && on.has(e.to) }]" marker-end="url(#map-arrow)" :d="e.d"><title>{{ e.type }}{{ e.labels.length ? `: ${e.labels.join(", ")}` : "" }}</title></path>
        </svg>
        <div v-for="col in L.cols" :key="col.label" class="map-col-head" :style="{ left: `${col.x}px` }">{{ col.label }} <b>{{ col.count }}</b></div>
        <button v-for="(p, id) in L.pos" :key="id" class="map-node" :class="{ missing: L.byId[id].missing, on: on?.has(id as string), sel: id === selected }"
          :style="{ left: `${p.x}px`, top: `${p.y}px`, width: `${MAP.W}px`, height: `${MAP.H}px` }" :aria-pressed="id === selected"
          @click="selected = id as string" @dblclick="L.byId[id].kind === 'Pod' && openDetail('Pod', L.byId[id].namespace, L.byId[id].name)" @mouseenter="hover = id as string">
          <span class="k-ic"><Icon :name="KIND_ICON[L.byId[id].kind] || 'dot'" /></span>
          <span class="txt"><span class="nm" style="display:block" :title="L.byId[id].name">{{ L.byId[id].name }}</span>
            <span class="dt" style="display:block">{{ L.byId[id].kind === "PersistentVolumeClaim" ? "PVC" : L.byId[id].kind }} · {{ L.byId[id].detail }}</span></span>
          <span v-if="['warning', 'critical'].includes(L.byId[id].status)" class="st" :class="L.byId[id].status" :title="L.byId[id].problem || ''"><Icon :name="L.byId[id].status === 'critical' ? 'crit' : 'warn'" /></span>
        </button>
      </div>
    </div></section>
    <aside class="card map-panel" aria-live="polite"><div class="card-body">
      <EmptyState v-if="!sel" title="Select anything on the map" sub="Click a card to see what it is connected to. Hover to preview. Double-click a pod for its logs." icon="map" />
      <template v-else>
        <div class="kind"><Icon :name="KIND_ICON[sel.kind] || 'dot'" />{{ sel.kind }}</div>
        <h3>{{ sel.name }}</h3>
        <div class="hint">{{ sel.detail }}</div>
        <div v-if="sel.problem" style="margin-top:8px"><span class="badge" :class="sel.status"><Icon :name="sel.status === 'critical' ? 'crit' : 'warn'" />{{ sel.problem }}</span></div>
        <div v-if="DETAIL_KINDS.has(sel.kind) && !sel.missing" style="margin-top:10px"><button class="btn sm" @click="openSel"><Icon name="file" /><span>{{ sel.kind === "Pod" ? "Details & logs" : "Open details" }}</span></button></div>
        <div v-for="(m, label) in relations" :key="label" class="rel"><h4>{{ label }} · {{ m.size }}</h4>
          <button v-for="[oid, how] in m" :key="oid" class="map-chip" @click="goto(oid)"><Icon :name="KIND_ICON[nodeOf(oid).kind] || 'dot'" /><span>{{ nodeOf(oid).name }}</span><i v-if="how.length">{{ how.join(", ") }}</i></button></div>
        <div v-if="!Object.keys(relations).length" class="hint" style="margin-top:12px">Not connected to anything in this namespace.</div>
      </template>
    </div></aside>
  </div>
</template>
