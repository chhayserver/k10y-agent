<script setup lang="ts">
import { to } from "@/lib/nav";
import { onMounted } from "vue";
import { useRouter } from "vue-router";
import Badge from "@/components/Badge.vue";
import EmptyState from "@/components/EmptyState.vue";
import Skeleton from "@/components/Skeleton.vue";
import { fmt, HEALTH } from "@/lib/format";
import { fleet, refreshFleet } from "@/stores/fleet";
import { useSession } from "@/stores/session";
const s = useSession(), router = useRouter();
onMounted(refreshFleet);
function open(id: number) { s.setCluster(id); router.push(to("overview")); }
</script>
<template>
  <div class="toolbar"><span class="result-count">{{ fmt.plural(s.clusters.length, "cluster") }}</span><span class="grow" />
    <button class="btn" @click="refreshFleet">Refresh</button>
    <RouterLink v-if="s.canGlobal('clusters.manage')" class="btn primary" :to="to('manage-clusters')">Manage clusters</RouterLink></div>
  <section v-if="!s.clusters.length" class="card"><div class="card-body">
    <EmptyState title="No clusters yet" :sub="s.canGlobal('clusters.manage') ? 'Add a cluster under Administration → Clusters.' : 'Ask an administrator to give you access to a cluster.'" icon="globe" /></div></section>
  <div v-else class="fleet">
    <section v-for="c in s.clusters" :key="c.id" class="card" :class="{ current: c.id === s.current?.id }">
      <div class="fleet-head"><div style="flex:1;min-width:0"><h3>{{ c.name }} <span v-if="c.id === s.current?.id" class="pill-current">current</span></h3></div>
        <span v-if="!fleet[c.id]" class="badge neutral">Checking…</span>
        <Badge v-else-if="fleet[c.id].error" level="critical" label="Unreachable" />
        <Badge v-else :level="HEALTH[fleet[c.id].summary.health].level" :label="HEALTH[fleet[c.id].summary.health].label" /></div>
      <template v-if="fleet[c.id]?.summary">
        <div class="fleet-stats"><div><span>Nodes</span><b>{{ fleet[c.id].summary.nodes.ready }}/{{ fleet[c.id].summary.nodes.total }}</b></div>
          <div><span>Pods</span><b>{{ fleet[c.id].summary.pods.total }}</b></div><div><span>Problems</span><b>{{ fleet[c.id].summary.problems.length }}</b></div></div>
        <div class="fleet-meters">
          <div class="row">CPU<div class="meter"><span :style="{ width: `${Math.min(fleet[c.id].summary.cpu.percent || 0, 100)}%` }" /></div><b>{{ fmt.pct(fleet[c.id].summary.cpu.percent) }}</b></div>
          <div class="row">Memory<div class="meter mem"><span :style="{ width: `${Math.min(fleet[c.id].summary.memory.percent || 0, 100)}%` }" /></div><b>{{ fmt.pct(fleet[c.id].summary.memory.percent) }}</b></div>
          <div class="hint">{{ fleet[c.id].summary.kubernetes_version }}</div></div>
      </template>
      <div v-else-if="fleet[c.id]?.error" class="fleet-err">{{ fleet[c.id].error }}</div>
      <Skeleton v-else :rows="3" />
      <div class="fleet-foot"><button class="btn sm" :class="{ primary: c.id !== s.current?.id }" @click="open(c.id)">{{ c.id === s.current?.id ? "Go to overview" : "Switch to cluster" }}</button></div>
    </section>
  </div>
</template>
