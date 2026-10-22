<script setup lang="ts">
import Badge from "@/components/Badge.vue";
import DataTable from "@/components/DataTable.vue";
import RowMenu from "@/components/RowMenu.vue";
import Skeleton from "@/components/Skeleton.vue";
import Toolbar from "@/components/Toolbar.vue";
import { menuFor } from "@/lib/actions";
import { fmt, podLabel, podLevel } from "@/lib/format";
import { useFiltered } from "@/lib/list";
import { useCluster } from "@/stores/cluster";
import { page } from "@/stores/prefs";
import { openDetail } from "@/stores/ui";
const c = useCluster(), state = page("pods");
const { rows, all, count } = useFiltered(() => c.data?.pods, state, (p: any) => `${p.name} ${p.namespace} ${p.node || ""} ${p.status}`,
  (p: any, f) => f === "all" || (f === "problems" ? Boolean(p.problem) : p.phase === "Running"));
const columns = [
  { key: "name", label: "Name", cls: "primary" }, { key: "namespace", label: "Namespace" }, { key: "status", label: "Status" },
  { key: "ready_containers", label: "Ready", num: true }, { key: "restarts", label: "Restarts", num: true }, { key: "cpu_usage_m", label: "CPU", num: true },
  { key: "memory_usage_bytes", label: "Memory", num: true }, { key: "node", label: "Node" }, { key: "age_s", label: "Age", num: true }, { key: "_a", label: "Actions", nosort: true, cls: "actions" },
];
</script>
<template>
  <Toolbar :state="state" placeholder="Search pods, nodes or status" :namespaces="all.map((p: any) => p.namespace)" :filters="[['all', 'All'], ['problems', 'Problems'], ['running', 'Running']]" :count="count" />
  <section class="card flush"><div class="card-body table-wrap">
    <Skeleton v-if="!c.data" :rows="8" />
    <DataTable v-else v-model:sort="state.sort" :rows="rows" :columns="columns" clickable
      :empty-title="state.filter === 'problems' && !state.q && !state.ns ? 'No problem pods' : 'No matching pods'" :empty-good="state.filter === 'problems' && !state.q && !state.ns"
      @row="(p) => openDetail('Pod', p.namespace, p.name)">
      <template #namespace="{ row }"><span class="ns">{{ row.namespace }}</span></template>
      <template #status="{ row }"><Badge :level="podLevel(row)" :label="podLabel(row)" /></template>
      <template #ready_containers="{ row }">{{ row.ready_containers }}/{{ row.total_containers }}</template>
      <template #restarts="{ row }"><b v-if="row.restarts" style="color:var(--text)">{{ row.restarts }}</b><span v-else>0</span></template>
      <template #cpu_usage_m="{ row }">{{ fmt.cpu(row.cpu_usage_m) }}</template>
      <template #memory_usage_bytes="{ row }">{{ fmt.bytes(row.memory_usage_bytes) }}</template>
      <template #node="{ row }">{{ row.node || "–" }}</template>
      <template #age_s="{ row }">{{ fmt.age(row.age_s) }}</template>
      <template #_a="{ row }"><RowMenu :items="() => menuFor('pod', row)" :label="row.name" /></template>
    </DataTable>
  </div></section>
</template>
