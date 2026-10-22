<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { agent } from "@/api/client";
import Badge from "@/components/Badge.vue";
import Chips from "@/components/Chips.vue";
import DataTable from "@/components/DataTable.vue";
import Icon from "@/components/Icon.vue";
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
const s = useSession(), c = useCluster(), state = page("namespaces", { key: "name", dir: 1 });
const list = ref<any[] | null>(null);
async function load() { try { list.value = await agent(s.current!.id).get("namespaces/details"); } catch { /* banner shows cluster errors */ } }
onMounted(load);
watch(() => c.lastUpdated, load);
const { rows, count } = useFiltered(() => list.value || undefined, state, (n: any) => `${n.name} ${n.status} ${Object.entries(n.labels).map(([k, v]) => `${k}=${v}`).join(" ")}`);
const columns = [
  { key: "name", label: "Name", cls: "primary" }, { key: "status", label: "Status" }, { key: "pods", label: "Pods", num: true },
  { key: "problems", label: "Problems", num: true }, { key: "deployments", label: "Deployments", num: true }, { key: "services", label: "Services", num: true },
  { key: "cpu_usage_m", label: "CPU", num: true }, { key: "memory_usage_bytes", label: "Memory", num: true },
  { key: "labels", label: "Labels", cls: "wrap-sm", sort: (n: any) => Object.keys(n.labels).join() }, { key: "age_s", label: "Age", num: true },
  { key: "_a", label: "Actions", nosort: true, cls: "actions" },
];
</script>
<template>
  <Toolbar :state="state" placeholder="Search namespaces or labels" :count="count" />
  <section class="card flush"><div class="card-body table-wrap">
    <Skeleton v-if="!list" :rows="6" />
    <DataTable v-else v-model:sort="state.sort" :rows="rows" :columns="columns" clickable empty-title="No matching namespaces" @row="(n) => openDetail('Namespace', '', n.name)">
      <template #name="{ row }">{{ row.name }} <span v-if="c.isProtected(row.name)" class="chip" title="Control actions are blocked here"><Icon name="lock" /> protected</span></template>
      <template #status="{ row }"><Badge :level="row.status === 'Active' ? 'good' : 'warning'" :label="row.status" /></template>
      <template #pods="{ row }">{{ row.running }} / {{ row.pods }}</template>
      <template #problems="{ row }"><Badge v-if="row.problems" level="critical" :label="row.problems" /><span v-else class="dim">0</span></template>
      <template #cpu_usage_m="{ row }">{{ fmt.cpu(row.cpu_usage_m) }}</template>
      <template #memory_usage_bytes="{ row }">{{ fmt.bytes(row.memory_usage_bytes) }}</template>
      <template #labels="{ row }"><Chips :items="row.labels" /></template>
      <template #age_s="{ row }">{{ fmt.age(row.age_s) }}</template>
      <template #_a="{ row }"><RowMenu :items="() => menuFor('namespace', row)" :label="row.name" /></template>
    </DataTable>
  </div></section>
</template>
