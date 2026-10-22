<script setup lang="ts">
import Badge from "@/components/Badge.vue";
import DataTable from "@/components/DataTable.vue";
import RowMenu from "@/components/RowMenu.vue";
import Skeleton from "@/components/Skeleton.vue";
import Toolbar from "@/components/Toolbar.vue";
import { menuFor } from "@/lib/actions";
import { fmt, type Level } from "@/lib/format";
import { useFiltered } from "@/lib/list";
import { useCluster } from "@/stores/cluster";
import { page } from "@/stores/prefs";
import { openDetail } from "@/stores/ui";
const c = useCluster(), state = page("hpas");
const { rows, all, count } = useFiltered(() => c.data?.hpas, state, (x: any) => `${x.name} ${x.namespace} ${x.target}`);
const columns = [{ key: "name", label: "Name", cls: "primary" }, { key: "namespace", label: "Namespace" }, { key: "target", label: "Scales" },
  { key: "status", label: "Status", sort: (x: any) => (x.problem ? 0 : x.limited ? 1 : 2) }, { key: "metrics", label: "Metrics (current / target)", nosort: true, cls: "wrap" },
  { key: "current", label: "Replicas", num: true }, { key: "range", label: "Min – max", sort: (x: any) => x.max }, { key: "age_s", label: "Age", num: true },
  { key: "_a", label: "Actions", nosort: true, cls: "actions" }];
function status(x: any): [Level, string] {
  if (x.problem) return ["warning", x.problem];
  if (x.limited) return ["serious", x.current >= x.max ? "At maximum" : "At minimum"];
  return ["good", "Scaling OK"];
}
const pct = (s: string) => (s && s.endsWith("%") ? Number(s.slice(0, -1)) : null);
</script>
<template>
  <Toolbar :state="state" placeholder="Search autoscalers or targets" :namespaces="all.map((x: any) => x.namespace)" :count="count" />
  <section class="card flush"><div class="card-body table-wrap">
    <Skeleton v-if="!c.data" :rows="5" />
    <DataTable v-else v-model:sort="state.sort" :rows="rows" :columns="columns" clickable :empty-title="all.length ? 'No matching autoscalers' : 'No HorizontalPodAutoscalers'" empty-icon="scale"
      @row="(x) => openDetail('HorizontalPodAutoscaler', x.namespace, x.name)">
      <template #namespace="{ row }"><span class="ns">{{ row.namespace }}</span></template>
      <template #target="{ row }"><button class="link" @click.stop="openDetail(row.target_kind, row.namespace, row.target_name)">{{ row.target }}</button></template>
      <template #status="{ row }"><Badge :level="status(row)[0]" :label="status(row)[1]" /></template>
      <template #metrics="{ row }"><div style="display:flex;flex-direction:column;gap:4px;min-width:180px">
        <div v-for="m in row.metrics" :key="m.type + m.name" style="display:flex;gap:8px;align-items:center;font-size:13px">
          <span style="min-width:60px">{{ m.name }}</span>
          <span class="mono" :style="{ color: (pct(m.current) ?? 0) > (pct(m.target) ?? Infinity) ? 'var(--warning-ink, var(--text))' : undefined, fontWeight: (pct(m.current) ?? 0) > (pct(m.target) ?? Infinity) ? 600 : undefined }">{{ m.current || "?" }}</span>
          <span class="dim mono">/ {{ m.target }}</span></div>
        <span v-if="!row.metrics.length" class="dim">–</span></div></template>
      <template #current="{ row }">{{ row.current ?? "–" }}<span v-if="row.current != null && row.desired && row.desired !== row.current" class="dim"> → {{ row.desired }}</span></template>
      <template #range="{ row }">{{ row.min }} – {{ row.max }}</template>
      <template #age_s="{ row }">{{ fmt.age(row.age_s) }}</template>
      <template #_a="{ row }"><RowMenu :items="() => menuFor('hpa', row)" :label="row.name" /></template>
    </DataTable>
  </div></section>
</template>
