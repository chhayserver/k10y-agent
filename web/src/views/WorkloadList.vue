<script setup lang="ts">
// Shared list for StatefulSets, DaemonSets and Jobs.
import Badge from "@/components/Badge.vue";
import Chips from "@/components/Chips.vue";
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
const props = defineProps<{ id: "statefulsets" | "daemonsets" | "jobs"; kind: string; menuKind: string; placeholder: string }>();
const c = useCluster(), state = page(props.id);
const { rows, all, count } = useFiltered(() => c.data?.[props.id], state, (x: any) => `${x.name} ${x.namespace} ${x.images.join(" ")} ${x.status || ""} ${x.owner || ""}`);
const JOB: Record<string, string> = { Complete: "good", Failed: "critical", Running: "pending", Suspended: "neutral" };
const base = [{ key: "name", label: "Name", cls: "primary" }, { key: "namespace", label: "Namespace" }];
const tail = [{ key: "images", label: "Image", sort: (x: any) => x.images[0] }, { key: "age_s", label: "Age", num: true }, { key: "_a", label: "Actions", nosort: true, cls: "actions" }];
const middle: Record<string, any[]> = {
  statefulsets: [{ key: "status", label: "Status", sort: (x: any) => (x.ready >= x.desired ? 1 : 0) }, { key: "ready", label: "Ready", num: true },
    { key: "updated", label: "Up-to-date", num: true }, { key: "service_name", label: "Service" }],
  daemonsets: [{ key: "status", label: "Status", sort: (x: any) => (x.ready >= x.desired ? 1 : 0) }, { key: "desired", label: "Desired", num: true },
    { key: "readyn", label: "Ready", num: true, sort: (x: any) => x.ready }, { key: "updated", label: "Up-to-date", num: true },
    { key: "available", label: "Available", num: true }, { key: "node_selector", label: "Node selector", cls: "wrap-sm", sort: (x: any) => Object.keys(x.node_selector).join() }],
  jobs: [{ key: "jobstatus", label: "Status", sort: (x: any) => x.status }, { key: "succeeded", label: "Completions", num: true },
    { key: "failed", label: "Failed pods", num: true }, { key: "duration_s", label: "Duration", num: true }, { key: "owner", label: "Created by" }],
};
const columns = [...base, ...middle[props.id], ...tail];
</script>
<template>
  <Toolbar :state="state" :placeholder="placeholder" :namespaces="all.map((x: any) => x.namespace)" :count="count" />
  <section class="card flush"><div class="card-body table-wrap">
    <Skeleton v-if="!c.data" :rows="5" />
    <DataTable v-else v-model:sort="state.sort" :rows="rows" :columns="columns" clickable :empty-title="all.length ? `No matching ${kind}s` : `No ${kind}s in this cluster`" @row="(x) => openDetail(kind, x.namespace, x.name)">
      <template #namespace="{ row }"><span class="ns">{{ row.namespace }}</span></template>
      <template #status="{ row }"><Badge :level="readyLevel(row)[0]" :label="readyLevel(row)[1]" /></template>
      <template #jobstatus="{ row }"><Badge :level="JOB[row.status] || 'neutral'" :label="row.status" /></template>
      <template #ready="{ row }">{{ row.ready }} / {{ row.desired }}</template>
      <template #readyn="{ row }">{{ row.ready }}</template>
      <template #succeeded="{ row }">{{ row.succeeded }} / {{ row.completions }}</template>
      <template #failed="{ row }"><b v-if="row.failed" style="color:var(--text)">{{ row.failed }}</b><span v-else>0</span></template>
      <template #duration_s="{ row }">{{ fmt.age(row.duration_s) }}</template>
      <template #service_name="{ row }">{{ row.service_name || "–" }}</template>
      <template #owner="{ row }">{{ row.owner || "–" }}</template>
      <template #node_selector="{ row }"><Chips :items="row.node_selector" /></template>
      <template #images="{ row }"><span class="truncate mono" style="max-width:240px" :title="row.images.join('\n')">{{ row.images.join(", ") }}</span></template>
      <template #age_s="{ row }">{{ fmt.age(row.age_s) }}</template>
      <template #_a="{ row }"><RowMenu :items="() => menuFor(menuKind, row)" :label="row.name" /></template>
    </DataTable>
  </div></section>
</template>
