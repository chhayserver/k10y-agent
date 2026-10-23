<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { hub } from "@/api/client";
import DataTable from "@/components/DataTable.vue";
import Icon from "@/components/Icon.vue";
import Skeleton from "@/components/Skeleton.vue";
import { fmt } from "@/lib/format";
import { to } from "@/lib/nav";
import { useHaproxy } from "@/stores/haproxy";
import { page } from "@/stores/prefs";
import { toast } from "@/stores/ui";
import HxInstanceDialog from "@/views/haproxy/HxInstanceDialog.vue";

const h = useHaproxy(), router = useRouter(), state = page("manage-haproxy", { key: "name", dir: 1 });
const list = ref<any[] | null>(null);
async function load() { list.value = await hub.get("/haproxy"); }
onMounted(load);
const dlg = reactive({ open: false, instance: null as any });
async function saved(id: number) { dlg.open = false; await load(); await h.loadInstances(id); }
async function remove(i: any) {
  if (!confirm(`Remove ${i.name} from k10y? HAProxy itself is not changed. Its config history and Kubernetes syncs in k10y are deleted.`)) return;
  try { await hub.del(`/haproxy/${i.id}`); toast(true, `Removed ${i.name}`); await load(); await h.loadInstances(); } catch (e: any) { toast(false, e.message); }
}
function open(i: any) { h.select(i.id); router.push(to("haproxy-overview")); }
const columns = [{ key: "name", label: "Name", cls: "primary" }, { key: "url", label: "Data Plane API", cls: "mono" }, { key: "group_name", label: "Group" },
  { key: "log_tag", label: "Log tag", cls: "mono" }, { key: "created_at", label: "Added" }, { key: "_a", label: "Actions", nosort: true, cls: "actions" }];
</script>
<template>
  <div class="toolbar"><span class="hint">HAProxy load balancers k10y manages through the HAProxy Data Plane API. Passwords are stored encrypted.</span><span class="grow" />
    <button class="btn primary" @click="Object.assign(dlg, { open: true, instance: null })"><Icon name="lb" /><span>Add HAProxy</span></button></div>
  <section class="card flush"><div class="card-body table-wrap">
    <Skeleton v-if="!list" :rows="3" />
    <DataTable v-else v-model:sort="state.sort" :rows="list" :columns="columns" :row-key="(i) => String(i.id)" clickable empty-title="No HAProxy yet" empty-icon="lb"
      @row="(i) => Object.assign(dlg, { open: true, instance: i })">
      <template #group_name="{ row }"><span v-if="row.group_name" class="chip">{{ row.group_name }}</span><span v-else class="dim">–</span></template>
      <template #created_at="{ row }">{{ fmt.dateTime(row.created_at) }}</template>
      <template #_a="{ row }"><button class="btn ghost sm" @click="open(row)">Open</button>
        <button class="btn ghost sm" style="color:var(--critical)" @click="remove(row)">Remove</button></template>
    </DataTable>
  </div></section>
  <HxInstanceDialog :open="dlg.open" :instance="dlg.instance" @close="dlg.open = false" @saved="saved" />
</template>
