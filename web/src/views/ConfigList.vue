<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { agent } from "@/api/client";
import Chips from "@/components/Chips.vue";
import DataTable from "@/components/DataTable.vue";
import RowMenu from "@/components/RowMenu.vue";
import Skeleton from "@/components/Skeleton.vue";
import Toolbar from "@/components/Toolbar.vue";
import { menuFor } from "@/lib/actions";
import { fmt } from "@/lib/format";
import { useFiltered } from "@/lib/list";
import { useCluster } from "@/stores/cluster";
import { page } from "@/stores/prefs";
import { useSession } from "@/stores/session";
import { openDetail } from "@/stores/ui";
const props = defineProps<{ kind: "configmap" | "secret" }>();
const s = useSession(), c = useCluster(), plural = `${props.kind}s`, label = props.kind === "secret" ? "Secrets" : "ConfigMaps";
const state = page(plural, { key: "namespace", dir: 1 }, "all");
const list = ref<any[] | null>(null), error = ref("");
async function load() { try { list.value = await agent(s.current!.id).get(plural); error.value = ""; } catch (e: any) { error.value = e.message; } }
onMounted(load);
watch(() => c.lastUpdated, load);
const { rows, all, count } = useFiltered(() => list.value || undefined, state, (x: any) => `${x.name} ${x.namespace} ${x.keys.join(" ")} ${x.type || ""}`,
  (x: any, f) => f === "all" || (f === "used" ? x.used_by.length > 0 : !x.used_by.length));
const columns = [
  { key: "name", label: "Name", cls: "primary" }, { key: "namespace", label: "Namespace" },
  ...(props.kind === "secret" ? [{ key: "type", label: "Type", cls: "wrap-sm" }] : []),
  { key: "keys", label: "Keys", cls: "wrap-sm", sort: (x: any) => x.keys.length },
  ...(props.kind === "configmap" ? [{ key: "size_bytes", label: "Size", num: true }] : []),
  { key: "used_by", label: "Used by", cls: "wrap-sm", sort: (x: any) => x.used_by.length }, { key: "age_s", label: "Age", num: true },
  { key: "_a", label: "Actions", nosort: true, cls: "actions" },
];
</script>
<template>
  <Toolbar :state="state" :placeholder="`Search ${label.toLowerCase()} or keys`" :namespaces="all.map((x: any) => x.namespace)" :filters="[['all', 'All'], ['used', 'In use'], ['unused', 'Unused']]" :count="count" />
  <section class="card flush"><div class="card-body table-wrap">
    <div v-if="error" class="banner show" style="margin:12px 18px">{{ error }}</div>
    <Skeleton v-if="!list && !error" :rows="6" />
    <DataTable v-else-if="list" v-model:sort="state.sort" :rows="rows" :columns="columns" clickable :empty-title="`No matching ${label}`" @row="(x) => openDetail(kind === 'secret' ? 'Secret' : 'ConfigMap', x.namespace, x.name)">
      <template #namespace="{ row }"><span class="ns">{{ row.namespace }}</span></template>
      <template #keys="{ row }"><Chips :keys="row.keys" :max="3" /></template>
      <template #size_bytes="{ row }">{{ row.size_bytes < 1024 ? `${row.size_bytes} B` : fmt.bytes(row.size_bytes) }}</template>
      <template #used_by="{ row }"><template v-if="row.used_by.length"><span v-for="w in row.used_by.slice(0, 2)" :key="w" class="chip" :title="w">{{ w.split("/")[1] }}</span><span v-if="row.used_by.length > 2" class="dim">+{{ row.used_by.length - 2 }}</span></template><span v-else class="dim">unused</span></template>
      <template #age_s="{ row }">{{ fmt.age(row.age_s) }}</template>
      <template #_a="{ row }"><RowMenu :items="() => menuFor(kind, row)" :label="row.name" /></template>
    </DataTable>
  </div></section>
  <p v-if="kind === 'secret'" class="hint" style="margin-top:12px">Only names, types and key names are shown. Secret values never leave the cluster through k10y.</p>
</template>
