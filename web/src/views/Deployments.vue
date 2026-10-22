<script setup lang="ts">
import Badge from "@/components/Badge.vue";
import DataTable from "@/components/DataTable.vue";
import RowMenu from "@/components/RowMenu.vue";
import Skeleton from "@/components/Skeleton.vue";
import Toolbar from "@/components/Toolbar.vue";
import { menuFor } from "@/lib/actions";
import { fmt, readyLevel } from "@/lib/format";
import { useFiltered } from "@/lib/list";
import { useCluster } from "@/stores/cluster";
import { page } from "@/stores/prefs";
import { openDetail } from "@/stores/ui";
const c = useCluster(), state = page("deployments");
const { rows, all, count } = useFiltered(() => c.data?.deployments, state, (d: any) => `${d.name} ${d.namespace} ${d.images.join(" ")}`);
const columns = [
  { key: "name", label: "Name", cls: "primary" }, { key: "namespace", label: "Namespace" }, { key: "status", label: "Status", sort: (d: any) => (d.ready >= d.desired ? 1 : 0) },
  { key: "revision", label: "Revision", num: true, sort: (d: any) => +d.revision || 0 }, { key: "ready", label: "Ready", num: true },
  { key: "updated", label: "Up-to-date", num: true }, { key: "available", label: "Available", num: true },
  { key: "images", label: "Image", sort: (d: any) => d.images[0] }, { key: "age_s", label: "Age", num: true }, { key: "_a", label: "Actions", nosort: true, cls: "actions" },
];
</script>
<template>
  <Toolbar :state="state" placeholder="Search deployments or images" :namespaces="all.map((d: any) => d.namespace)" :count="count" />
  <section class="card flush"><div class="card-body table-wrap">
    <Skeleton v-if="!c.data" :rows="5" />
    <DataTable v-else v-model:sort="state.sort" :rows="rows" :columns="columns" clickable empty-title="No matching deployments" empty-sub="Try a different search or namespace." @row="(d) => openDetail('Deployment', d.namespace, d.name)">
      <template #namespace="{ row }"><span class="ns">{{ row.namespace }}</span></template>
      <template #status="{ row }"><Badge :level="readyLevel(row)[0]" :label="readyLevel(row)[1] === 'Ready' ? 'Available' : readyLevel(row)[1]" /></template>
      <template #revision="{ row }">{{ row.revision ? `#${row.revision}` : "–" }}</template>
      <template #ready="{ row }">{{ row.ready }} / {{ row.desired }}</template>
      <template #images="{ row }"><span class="truncate mono" style="max-width:240px" :title="row.images.join('\n')">{{ row.images.join(", ") }}</span></template>
      <template #age_s="{ row }">{{ fmt.age(row.age_s) }}</template>
      <template #_a="{ row }"><RowMenu :items="() => menuFor('deployment', row)" :label="row.name" /></template>
    </DataTable>
  </div></section>
</template>
