<script setup lang="ts">
import Badge from "@/components/Badge.vue";
import DataTable from "@/components/DataTable.vue";
import RowMenu from "@/components/RowMenu.vue";
import Skeleton from "@/components/Skeleton.vue";
import Toolbar from "@/components/Toolbar.vue";
import { menuFor } from "@/lib/actions";
import { fmt } from "@/lib/format";
import { useFiltered } from "@/lib/list";
import { useCluster } from "@/stores/cluster";
import { page } from "@/stores/prefs";
import { openDetail } from "@/stores/ui";
const c = useCluster(), state = page("cronjobs");
const { rows, all, count } = useFiltered(() => c.data?.cronjobs, state, (x: any) => `${x.name} ${x.namespace} ${x.schedule} ${x.images.join(" ")}`,
  (x, f) => f === "all" || (f === "suspended" ? x.suspended : f === "active" ? x.active > 0 : true));
const columns = [{ key: "name", label: "Name", cls: "primary" }, { key: "namespace", label: "Namespace" },
  { key: "schedule", label: "Schedule", cls: "mono" }, { key: "suspended", label: "Status", sort: (x: any) => (x.suspended ? 1 : x.active ? 0 : 2) },
  { key: "active", label: "Running", num: true }, { key: "last_schedule_s", label: "Last run", num: true }, { key: "last_success_s", label: "Last success", num: true },
  { key: "images", label: "Image", sort: (x: any) => x.images[0] }, { key: "age_s", label: "Age", num: true }, { key: "_a", label: "Actions", nosort: true, cls: "actions" }];
// "*/5 * * * *" -> "every 5 minutes"; anything unusual stays as the raw expression.
function human(s: string) {
  const p = s.trim().split(/\s+/);
  if (s.startsWith("@")) return s.slice(1);
  if (p.length !== 5) return "";
  const [m, h, dom, mon, dow] = p;
  if (dom !== "*" || mon !== "*") return "";
  const days = dow === "*" ? "" : dow === "1-5" ? " on weekdays" : ` on day ${dow}`;
  if (/^\*\/\d+$/.test(m) && h === "*") return `every ${m.slice(2)} minutes${days}`;
  if (m === "*" && h === "*") return `every minute${days}`;
  if (/^\d+$/.test(m) && h === "*") return `hourly at :${m.padStart(2, "0")}${days}`;
  if (/^\d+$/.test(m) && /^\d+$/.test(h)) return `daily at ${h.padStart(2, "0")}:${m.padStart(2, "0")}${days}`;
  return "";
}
</script>
<template>
  <Toolbar :state="state" placeholder="Search cronjobs, schedule or images" :namespaces="all.map((x: any) => x.namespace)" :filters="[['all', 'All'], ['active', 'Running'], ['suspended', 'Suspended']]" :count="count" />
  <section class="card flush"><div class="card-body table-wrap">
    <Skeleton v-if="!c.data" :rows="5" />
    <DataTable v-else v-model:sort="state.sort" :rows="rows" :columns="columns" clickable :empty-title="all.length ? 'No matching CronJobs' : 'No CronJobs in this cluster'" empty-icon="pending" @row="(x) => openDetail('CronJob', x.namespace, x.name)">
      <template #namespace="{ row }"><span class="ns">{{ row.namespace }}</span></template>
      <template #schedule="{ row }"><div><code>{{ row.schedule }}</code><div v-if="human(row.schedule)" class="dim" style="font-size:12px;font-family:var(--font)">{{ human(row.schedule) }}{{ row.timezone ? ` (${row.timezone})` : "" }}</div></div></template>
      <template #suspended="{ row }"><Badge v-if="row.suspended" level="neutral" label="Suspended" /><Badge v-else-if="row.active" level="pending" label="Running" /><Badge v-else level="good" label="Scheduled" /></template>
      <template #last_schedule_s="{ row }">{{ row.last_schedule_s == null ? "never" : `${fmt.age(row.last_schedule_s)} ago` }}</template>
      <template #last_success_s="{ row }">{{ row.last_success_s == null ? "–" : `${fmt.age(row.last_success_s)} ago` }}</template>
      <template #images="{ row }"><span class="truncate mono" style="max-width:220px" :title="row.images.join('\n')">{{ row.images.join(", ") }}</span></template>
      <template #age_s="{ row }">{{ fmt.age(row.age_s) }}</template>
      <template #_a="{ row }"><RowMenu :items="() => menuFor('cronjob', row)" :label="row.name" /></template>
    </DataTable>
  </div></section>
</template>
