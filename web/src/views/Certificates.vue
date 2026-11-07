<script setup lang="ts">
// TLS certificates in use: those of Ingress TLS secrets, and cert-manager Certificates, with days until they expire.
import { onMounted, ref, watch } from "vue";
import { agent } from "@/api/client";
import Badge from "@/components/Badge.vue";
import DataTable from "@/components/DataTable.vue";
import Icon from "@/components/Icon.vue";
import Skeleton from "@/components/Skeleton.vue";
import Toolbar from "@/components/Toolbar.vue";
import { useFiltered } from "@/lib/list";
import { page } from "@/stores/prefs";
import { useSession } from "@/stores/session";

const s = useSession(), state = page("certificates", { key: "days_left", dir: 1 });
const list = ref<any[] | null>(null), error = ref("");
async function load(fresh = false) {
  try { list.value = await agent(s.current!.id).get("certificates", fresh ? { fresh: true } : undefined); error.value = ""; } catch (e: any) { error.value = e.message; }
}
onMounted(() => load());
watch(() => s.current?.id, () => load());
const level = (x: any) => (x.error || x.ready === false ? "critical" : x.days_left == null ? "neutral" : x.days_left < 0 ? "critical" : x.days_left < 14 ? "warning" : "good");
const label = (x: any) => (x.error ? "Can't read" : x.ready === false ? "Not ready" : x.days_left == null ? "Unknown"
  : x.days_left < 0 ? `Expired ${-x.days_left} d ago` : x.days_left === 0 ? "Expires today" : `${x.days_left} days left`);
const { rows, all, count } = useFiltered(() => list.value || undefined, state, (x: any) => `${x.name} ${x.namespace} ${(x.hosts || []).join(" ")} ${x.issuer || ""}`,
  (x, f) => f === "all" || (f === "soon" ? ["warning", "critical"].includes(level(x)) : true));
const columns = [{ key: "days_left", label: "Expiry", sort: (x: any) => (x.days_left ?? 1e6) }, { key: "hosts", label: "Hosts", cls: "wrap", sort: (x: any) => (x.hosts || [])[0] || "" },
  { key: "namespace", label: "Namespace" }, { key: "name", label: "Certificate / secret", cls: "primary" }, { key: "source", label: "From" },
  { key: "issuer", label: "Issuer" }, { key: "not_after", label: "Valid until", sort: (x: any) => x.not_after || "" }];
</script>

<template>
  <Toolbar :state="state" placeholder="Search hosts, names or issuers" :namespaces="all.map((x: any) => x.namespace)" :filters="[['all', 'All'], ['soon', 'Expiring or broken']]" :count="count">
    <button class="btn" type="button" @click="load(true)"><Icon name="refresh" /><span>Check now</span></button></Toolbar>
  <div v-if="error" class="banner show"><Icon name="crit" /><span class="msg">{{ error }}</span></div>
  <section class="card flush"><div class="card-body table-wrap">
    <Skeleton v-if="!list && !error" :rows="4" />
    <DataTable v-else-if="list" v-model:sort="state.sort" :rows="rows" :columns="columns"
      :empty-title="all.length ? 'No matching certificates' : 'No TLS certificates found'" empty-icon="lock">
      <template #days_left="{ row }"><Badge :level="level(row)" :label="label(row)" /><div v-if="row.error" class="dim" style="font-size:12px">{{ row.error }}</div></template>
      <template #hosts="{ row }"><span class="mono">{{ (row.hosts || []).join(", ") || "–" }}</span></template>
      <template #namespace="{ row }"><span class="ns">{{ row.namespace }}</span></template>
      <template #source="{ row }"><span class="chip">{{ row.source }}</span><div v-if="row.ingress" class="dim" style="font-size:12px">Ingress {{ row.ingress }}</div></template>
      <template #issuer="{ row }">{{ row.issuer || "–" }}</template>
      <template #not_after="{ row }"><span class="dim">{{ row.not_after ? new Date(row.not_after).toLocaleDateString() : "–" }}</span></template>
    </DataTable>
  </div></section>
  <p class="hint" style="margin-top:12px">Certificates of Ingress TLS secrets (only their expiry and names are read, never the key) and cert-manager Certificates. Alerts warn 14 days before a certificate expires.</p>
</template>
