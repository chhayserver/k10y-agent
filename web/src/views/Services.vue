<script setup lang="ts">
import Chips from "@/components/Chips.vue";
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
const c = useCluster(), state = page("services");
const portText = (p: any) => `${p.port}${p.target_port && p.target_port !== String(p.port) ? `→${p.target_port}` : ""}/${p.protocol}`;
const { rows, all, count } = useFiltered(() => c.data?.services, state, (x: any) => `${x.name} ${x.namespace} ${x.type} ${x.cluster_ip} ${x.ports.map((p: any) => `${portText(p)} ${p.node_port || ""}`).join(" ")}`);
const host = () => (c.data?.nodes || []).find((n: any) => n.ready && n.internal_ip)?.internal_ip || location.hostname;
const columns = [
  { key: "name", label: "Name", cls: "primary" }, { key: "namespace", label: "Namespace" }, { key: "type", label: "Type" },
  { key: "cluster_ip", label: "Cluster IP", cls: "mono" }, { key: "ports", label: "Ports", sort: (x: any) => x.ports[0]?.port },
  { key: "access", label: "NodePort URL", sort: (x: any) => x.ports.find((p: any) => p.node_port)?.node_port },
  { key: "selector", label: "Selector", cls: "wrap-sm", sort: (x: any) => Object.keys(x.selector).join() }, { key: "age_s", label: "Age", num: true },
  { key: "_a", label: "Actions", nosort: true, cls: "actions" },
];
</script>
<template>
  <Toolbar :state="state" placeholder="Search services, IPs or ports" :namespaces="all.map((x: any) => x.namespace)" :count="count" />
  <section class="card flush"><div class="card-body table-wrap">
    <Skeleton v-if="!c.data" :rows="6" />
    <DataTable v-else v-model:sort="state.sort" :rows="rows" :columns="columns" clickable empty-title="No matching services" @row="(x) => openDetail('Service', x.namespace, x.name)">
      <template #namespace="{ row }"><span class="ns">{{ row.namespace }}</span></template>
      <template #ports="{ row }"><span class="port">{{ row.ports.map(portText).join(", ") || "–" }}</span></template>
      <template #access="{ row }">
        <template v-if="row.ports.some((p: any) => p.node_port)"><div v-for="p in row.ports.filter((p: any) => p.node_port)" :key="p.node_port">
          <a class="ext-link" :href="`${p.port === 443 || p.name === 'https' ? 'https' : 'http'}://${host()}:${p.node_port}`" target="_blank" rel="noopener">{{ host() }}:{{ p.node_port }}<Icon name="external" /></a></div></template>
        <span v-else class="dim">cluster only</span></template>
      <template #selector="{ row }"><Chips :items="row.selector" /></template>
      <template #age_s="{ row }">{{ fmt.age(row.age_s) }}</template>
      <template #_a="{ row }"><RowMenu :items="() => menuFor('service', row)" :label="row.name" /></template>
    </DataTable>
  </div></section>
</template>
