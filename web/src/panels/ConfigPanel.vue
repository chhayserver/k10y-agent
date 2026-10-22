<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { agent } from "@/api/client";
import Icon from "@/components/Icon.vue";
import Skeleton from "@/components/Skeleton.vue";
import { showOnMap } from "@/lib/actions";
import { fmt, KIND_ICON } from "@/lib/format";
import { useSession } from "@/stores/session";
import { openDetail, type PanelRef } from "@/stores/ui";
import PanelHead from "./PanelHead.vue";

const props = defineProps<{ r: PanelRef }>();
const s = useSession();
const isCm = computed(() => props.r.kind === "ConfigMap");
const data = ref<any>(null), error = ref("");
onMounted(async () => {
  const a = agent(s.current!.id);
  try {
    if (isCm.value) data.value = await a.get(`configmaps/${props.r.namespace}/${props.r.name}`);
    else data.value = (await a.get("secrets", { namespace: props.r.namespace })).find((x: any) => x.name === props.r.name) || null;
    if (!data.value) error.value = "Not found. It may have been deleted.";
  } catch (e: any) { error.value = e.message; }
});
const copied = ref("");
async function copy(k: string) { try { await navigator.clipboard.writeText(data.value.data[k]); copied.value = k; setTimeout(() => (copied.value = ""), 1500); } catch { /* denied */ } }
</script>
<template>
  <PanelHead :kind="r.kind" :name="r.name" :namespace="r.namespace">
    <template #actions>
      <button class="btn" @click="openDetail(r.kind, r.namespace, r.name, { push: true, tab: 'yaml' })"><Icon name="file" /><span>YAML</span></button>
      <button class="btn" @click="showOnMap(r.kind, r)"><Icon name="map" /><span>Show on map</span></button>
    </template>
  </PanelHead>
  <div class="drawer-body">
    <div v-if="error" class="empty"><Icon name="crit" /><div class="title">Could not load {{ r.kind }}</div><div>{{ error }}</div></div>
    <Skeleton v-else-if="!data" :rows="4" />
    <template v-else>
      <div v-if="!isCm" class="kv"><div><span>Type</span><b>{{ data.type }}</b></div><div><span>Age</span><b>{{ fmt.age(data.age_s) }}</b></div><div><span>Keys</span><b>{{ data.keys.length }}</b></div></div>
      <div><h3 class="section-title">Used by</h3>
        <ul v-if="data.used_by.length" class="list card" style="box-shadow:none">
          <li v-for="w in data.used_by" :key="w" class="clickable" @click="openDetail(w.split('/')[0], r.namespace, w.split('/')[1], { push: true })">
            <span class="ic"><Icon :name="KIND_ICON[w.split('/')[0]] || 'pods'" /></span><div><div class="main-line">{{ w.split("/")[1] }}</div><div class="sub-line">{{ w.split("/")[0] }}</div></div></li>
        </ul>
        <p v-else class="hint" style="margin:0">No running pod references it.</p></div>
      <div v-if="isCm"><h3 class="section-title">Data · {{ Object.keys(data.data).length + data.binary_keys.length }} keys</h3>
        <div v-for="k in Object.keys(data.data).sort()" :key="k" style="margin-bottom:14px">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px"><code class="action">{{ k }}</code><span class="hint">{{ data.data[k].length }} chars</span><span style="flex:1" />
            <button class="btn sm" @click="copy(k)"><Icon name="copy" /><span>{{ copied === k ? "Copied" : "Copy" }}</span></button></div>
          <pre class="logs wrap" style="min-height:0;max-height:260px">{{ data.data[k] || "(empty)" }}</pre>
        </div>
        <div v-for="k in data.binary_keys" :key="k"><code class="action">{{ k }}</code> <span class="hint">binary data, not shown</span></div>
      </div>
      <div v-else><h3 class="section-title">Keys</h3>
        <ul v-if="data.keys.length" class="list card" style="box-shadow:none"><li v-for="k in data.keys" :key="k"><span class="ic"><Icon name="key" /></span>
          <div style="flex:1"><div class="main-line" style="font-family:var(--mono)">{{ k }}</div></div><span class="meta">••••••••</span></li></ul>
        <p class="hint" style="margin-top:10px">Values are never shown.</p></div>
    </template>
  </div>
</template>
