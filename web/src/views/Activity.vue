<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { hub } from "@/api/client";
import Badge from "@/components/Badge.vue";
import DataTable from "@/components/DataTable.vue";
import Skeleton from "@/components/Skeleton.vue";
import Toolbar from "@/components/Toolbar.vue";
import { fmt } from "@/lib/format";
import { page } from "@/stores/prefs";
import { useSession } from "@/stores/session";
const s = useSession(), state = page("activity", { key: "id", dir: -1 }, "all");
const list = ref<any[] | null>(null), error = ref(""), scope = ref<"all" | "current">("all");
async function load() {
  try { list.value = await hub.get("/audit", { limit: 500, cluster_id: scope.value === "current" ? s.current?.id : undefined }); error.value = ""; }
  catch (e: any) { error.value = e.message; }
}
onMounted(load);
const rows = computed(() => {
  const q = state.q.trim().toLowerCase();
  return (list.value || []).filter((a) => (state.filter === "all" || (state.filter === "failed" ? !a.ok : state.filter === "changes" ? a.ok && !a.dry_run && !["login", "ai-diagnose"].includes(a.action) : true))
    && (!q || `${a.username} ${a.action} ${a.target} ${a.cluster} ${a.message}`.toLowerCase().includes(q)));
});
const columns = [
  { key: "id", label: "Time" }, { key: "username", label: "User", cls: "primary" }, { key: "cluster", label: "Cluster" },
  { key: "action", label: "Action" }, { key: "target", label: "Target" }, { key: "ok", label: "Result", sort: (a: any) => (a.ok ? (a.dry_run ? 1 : 2) : 0) },
  { key: "message", label: "Details", cls: "wrap" }, { key: "client_ip", label: "From" },
];
</script>
<template>
  <Toolbar :state="state" placeholder="Search user, action, target or message" :filters="[['all', 'All'], ['changes', 'Changes'], ['failed', 'Failed']]" :count="list ? `${rows.length} of ${list.length}` : ''">
    <div class="segmented" role="group" aria-label="Scope"><button :aria-pressed="scope === 'all'" @click="scope = 'all'; load()">All clusters</button>
      <button :aria-pressed="scope === 'current'" :disabled="!s.current" @click="scope = 'current'; load()">{{ s.current?.name || "Current" }}</button></div>
    <button class="btn" @click="load">Reload</button>
  </Toolbar>
  <div v-if="error" class="banner show">{{ error }}</div>
  <section class="card flush"><div class="card-body table-wrap">
    <Skeleton v-if="!list && !error" :rows="6" />
    <DataTable v-else-if="list" v-model:sort="state.sort" :rows="rows" :columns="columns" :row-key="(a) => String(a.id)" empty-title="No activity yet" empty-icon="audit">
      <template #id="{ row }">{{ fmt.dateTime(row.time) }}</template>
      <template #cluster="{ row }"><span v-if="row.cluster" class="ns">{{ row.cluster }}</span><span v-else class="dim">hub</span></template>
      <template #action="{ row }"><code class="action">{{ row.action }}</code></template>
      <template #target="{ row }"><span class="truncate" :title="row.target">{{ row.target }}</span></template>
      <template #ok="{ row }"><Badge v-if="!row.ok" level="critical" label="Failed" /><Badge v-else-if="row.dry_run" level="neutral" label="Dry run" /><Badge v-else level="good" label="Success" /></template>
      <template #client_ip="{ row }"><span class="dim">{{ row.client_ip }}</span></template>
    </DataTable>
  </div></section>
  <p class="hint" style="margin-top:12px">Stored permanently in the hub's PostgreSQL database, with the user who did each action. Includes dry runs, failed attempts, logins and admin changes.</p>
</template>
