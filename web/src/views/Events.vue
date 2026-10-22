<script setup lang="ts">
import Badge from "@/components/Badge.vue";
import DataTable from "@/components/DataTable.vue";
import Skeleton from "@/components/Skeleton.vue";
import Toolbar from "@/components/Toolbar.vue";
import { DETAIL_KINDS, fmt } from "@/lib/format";
import { useFiltered } from "@/lib/list";
import { useCluster } from "@/stores/cluster";
import { page } from "@/stores/prefs";
import { openDetail, toast } from "@/stores/ui";
const c = useCluster(), state = page("events", { key: "age_s", dir: 1 }, "Warning");
const { rows, all, count } = useFiltered(() => c.data?.events, state, (e: any) => `${e.reason} ${e.object} ${e.message}`, (e: any, f) => !f || f === "all" || e.type === f);
const columns = [
  { key: "age_s", label: "Last seen" }, { key: "type", label: "Type" }, { key: "reason", label: "Reason", cls: "primary" },
  { key: "object", label: "Object" }, { key: "namespace", label: "Namespace" }, { key: "message", label: "Message", cls: "wrap" }, { key: "count", label: "Count", num: true },
];
function open(e: any) { const [kind, name] = e.object.split("/"); if (DETAIL_KINDS.has(kind)) openDetail(kind, e.namespace, name); else toast(false, `No details view for ${kind}`); }
</script>
<template>
  <Toolbar :state="state" placeholder="Search reason, object or message" :namespaces="all.map((e: any) => e.namespace).filter(Boolean)" :filters="[['Warning', 'Warnings'], ['all', 'All events']]" :count="count" />
  <section class="card flush"><div class="card-body table-wrap">
    <Skeleton v-if="!c.data" :rows="8" />
    <DataTable v-else v-model:sort="state.sort" :rows="rows" :columns="columns" clickable :row-key="(e) => `${e.object}/${e.reason}/${e.timestamp}`"
      :empty-title="state.filter === 'Warning' ? 'No warning events' : 'No matching events'" :empty-good="state.filter === 'Warning'" @row="open">
      <template #age_s="{ row }">{{ fmt.age(row.age_s) }} ago</template>
      <template #type="{ row }"><Badge :level="row.type === 'Warning' ? 'warning' : 'neutral'" :label="row.type || 'Normal'" /></template>
      <template #object="{ row }"><span class="truncate" :title="row.object">{{ row.object }}</span></template>
      <template #namespace="{ row }"><span v-if="row.namespace" class="ns">{{ row.namespace }}</span><span v-else>–</span></template>
    </DataTable>
  </div></section>
</template>
