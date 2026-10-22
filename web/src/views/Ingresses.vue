<script setup lang="ts">
import DataTable from "@/components/DataTable.vue";
import Icon from "@/components/Icon.vue";
import RowMenu from "@/components/RowMenu.vue";
import Skeleton from "@/components/Skeleton.vue";
import Toolbar from "@/components/Toolbar.vue";
import { menuFor } from "@/lib/actions";
import { fmt } from "@/lib/format";
import { useFiltered } from "@/lib/list";
import { useCluster } from "@/stores/cluster";
import { page } from "@/stores/prefs";
import { openDetail } from "@/stores/ui";
const c = useCluster(), state = page("ingresses");
const { rows, all, count } = useFiltered(() => c.data?.ingresses, state,
  (x: any) => `${x.name} ${x.namespace} ${x.hosts.join(" ")} ${x.rules.map((r: any) => r.service).join(" ")} ${x.class || ""}`);
const columns = [{ key: "name", label: "Name", cls: "primary" }, { key: "namespace", label: "Namespace" }, { key: "class", label: "Class" },
  { key: "rules", label: "Routes", cls: "wrap", sort: (x: any) => x.hosts[0] }, { key: "tls_hosts", label: "TLS", sort: (x: any) => x.tls_hosts.length },
  { key: "address", label: "Address", sort: (x: any) => x.address[0] }, { key: "age_s", label: "Age", num: true }, { key: "_a", label: "Actions", nosort: true, cls: "actions" }];
const url = (x: any, r: any) => (r.host === "*" ? "" : `${x.tls_hosts.includes(r.host) ? "https" : "http"}://${r.host}${r.path}`);
</script>
<template>
  <Toolbar :state="state" placeholder="Search ingresses, hosts or services" :namespaces="all.map((x: any) => x.namespace)" :count="count" />
  <section class="card flush"><div class="card-body table-wrap">
    <Skeleton v-if="!c.data" :rows="5" />
    <DataTable v-else v-model:sort="state.sort" :rows="rows" :columns="columns" clickable :empty-title="all.length ? 'No matching Ingresses' : 'No Ingresses in this cluster'" empty-icon="globe" @row="(x) => openDetail('Ingress', x.namespace, x.name)">
      <template #namespace="{ row }"><span class="ns">{{ row.namespace }}</span></template>
      <template #class="{ row }">{{ row.class || "–" }}</template>
      <template #rules="{ row }"><div style="display:flex;flex-direction:column;gap:2px">
        <div v-for="(r, i) in row.rules" :key="i" style="font-size:13px"><a v-if="url(row, r)" :href="url(row, r)" target="_blank" rel="noopener" class="mono">{{ r.host }}{{ r.path }}</a><span v-else class="mono">*{{ r.path }}</span>
          <span class="dim"> → </span><button class="link" @click.stop="openDetail('Service', row.namespace, r.service)">{{ r.service }}</button><span class="dim">{{ r.port ? `:${r.port}` : "" }}</span></div>
        <div v-if="!row.rules.length && row.default_backend" style="font-size:13px"><span class="dim">default → </span>{{ row.default_backend }}</div></div></template>
      <template #tls_hosts="{ row }"><span v-if="row.tls_hosts.length" class="chip"><Icon name="lock" /> TLS</span><span v-else class="dim">–</span></template>
      <template #address="{ row }"><span class="mono">{{ row.address.join(", ") || "–" }}</span></template>
      <template #age_s="{ row }">{{ fmt.age(row.age_s) }}</template>
      <template #_a="{ row }"><RowMenu :items="() => menuFor('ingress', row)" :label="row.name" /></template>
    </DataTable>
  </div></section>
</template>
