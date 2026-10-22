<script setup lang="ts">
import { computed, ref } from "vue";
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
const c = useCluster(), state = page("storage"), pvState = page("storage-pv", { key: "name", dir: 1 });
const view = ref<"pvc" | "pv">("pvc");
const PHASE: Record<string, Level> = { Bound: "good", Available: "neutral", Released: "warning", Pending: "pending", Failed: "critical", Lost: "critical" };
const pvcs = useFiltered(() => c.data?.pvcs, state, (x: any) => `${x.name} ${x.namespace} ${x.volume || ""} ${x.storage_class || ""} ${x.status}`);
const pvs = useFiltered(() => c.data?.pvs, pvState, (x: any) => `${x.name} ${x.claim || ""} ${x.storage_class || ""} ${x.status}`);
const cur = computed(() => (view.value === "pvc" ? pvcs : pvs));
const pvcColumns = [{ key: "name", label: "Name", cls: "primary" }, { key: "namespace", label: "Namespace" }, { key: "status", label: "Status" },
  { key: "capacity", label: "Size", num: true, sort: (x: any) => x.capacity_bytes }, { key: "access_modes", label: "Access", sort: (x: any) => x.access_modes.join() },
  { key: "storage_class", label: "Storage class" }, { key: "volume", label: "Volume", cls: "mono" }, { key: "age_s", label: "Age", num: true }, { key: "_a", label: "Actions", nosort: true, cls: "actions" }];
const pvColumns = [{ key: "name", label: "Name", cls: "primary mono" }, { key: "status", label: "Status" }, { key: "capacity", label: "Size", num: true, sort: (x: any) => x.capacity_bytes },
  { key: "access_modes", label: "Access", sort: (x: any) => x.access_modes.join() }, { key: "reclaim_policy", label: "Reclaim" }, { key: "claim", label: "Claim" },
  { key: "storage_class", label: "Storage class" }, { key: "age_s", label: "Age", num: true }, { key: "_a", label: "Actions", nosort: true, cls: "actions" }];
const MODE: Record<string, string> = { ReadWriteOnce: "RWO", ReadOnlyMany: "ROX", ReadWriteMany: "RWX", ReadWriteOncePod: "RWOP" };
const total = computed(() => fmt.bytes((c.data?.pvcs || []).reduce((a: number, x: any) => a + (x.capacity_bytes || 0), 0)));
</script>
<template>
  <Toolbar :state="view === 'pvc' ? state : pvState" :placeholder="view === 'pvc' ? 'Search claims, volumes or storage class' : 'Search volumes, claims or storage class'"
    :namespaces="view === 'pvc' ? pvcs.all.value.map((x: any) => x.namespace) : undefined" :count="cur.count.value">
    <div class="segmented" role="group" aria-label="Kind">
      <button :aria-pressed="view === 'pvc'" @click="view = 'pvc'">Claims (PVC)</button><button :aria-pressed="view === 'pv'" @click="view = 'pv'">Volumes (PV)</button></div>
    <span v-if="c.data && view === 'pvc'" class="hint">{{ total }} claimed</span>
  </Toolbar>
  <section class="card flush"><div class="card-body table-wrap">
    <Skeleton v-if="!c.data" :rows="5" />
    <DataTable v-else-if="view === 'pvc'" v-model:sort="state.sort" :rows="pvcs.rows.value" :columns="pvcColumns" clickable :empty-title="pvcs.all.value.length ? 'No matching claims' : 'No PersistentVolumeClaims'" empty-icon="disk"
      @row="(x) => openDetail('PersistentVolumeClaim', x.namespace, x.name)">
      <template #namespace="{ row }"><span class="ns">{{ row.namespace }}</span></template>
      <template #status="{ row }"><Badge :level="PHASE[row.status] || 'neutral'" :label="row.status || '–'" /></template>
      <template #capacity="{ row }">{{ row.capacity || "–" }}</template>
      <template #access_modes="{ row }"><span v-for="m in row.access_modes" :key="m" class="chip" :title="m">{{ MODE[m] || m }}</span></template>
      <template #storage_class="{ row }">{{ row.storage_class || "–" }}</template>
      <template #volume="{ row }"><button v-if="row.volume" class="link mono" @click.stop="openDetail('PersistentVolume', '', row.volume)">{{ row.volume }}</button><span v-else class="dim">–</span></template>
      <template #age_s="{ row }">{{ fmt.age(row.age_s) }}</template>
      <template #_a="{ row }"><RowMenu :items="() => menuFor('pvc', row)" :label="row.name" /></template>
    </DataTable>
    <DataTable v-else v-model:sort="pvState.sort" :rows="pvs.rows.value" :columns="pvColumns" clickable :empty-title="pvs.all.value.length ? 'No matching volumes' : 'No PersistentVolumes'" empty-icon="disk"
      @row="(x) => openDetail('PersistentVolume', '', x.name)">
      <template #status="{ row }"><Badge :level="PHASE[row.status] || 'neutral'" :label="row.status || '–'" /></template>
      <template #capacity="{ row }">{{ row.capacity || "–" }}</template>
      <template #access_modes="{ row }"><span v-for="m in row.access_modes" :key="m" class="chip" :title="m">{{ MODE[m] || m }}</span></template>
      <template #claim="{ row }"><button v-if="row.claim" class="link" @click.stop="openDetail('PersistentVolumeClaim', row.claim.split('/')[0], row.claim.split('/')[1])">{{ row.claim }}</button><span v-else class="dim">–</span></template>
      <template #storage_class="{ row }">{{ row.storage_class || "–" }}</template>
      <template #age_s="{ row }">{{ fmt.age(row.age_s) }}</template>
      <template #_a="{ row }"><RowMenu :items="() => menuFor('pv', row)" :label="row.name" /></template>
    </DataTable>
  </div></section>
</template>
