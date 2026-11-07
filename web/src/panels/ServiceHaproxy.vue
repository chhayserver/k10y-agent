<script setup lang="ts">
// Which HAProxy load balancers send traffic to this Service: backends linked by a Kubernetes sync, or whose
// servers are the Service's node ports or pod IPs. With the frontends that use them and their live numbers.
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { hub } from "@/api/client";
import Badge from "@/components/Badge.vue";
import { fmtRate, STATE } from "@/lib/haproxy";
import { to } from "@/lib/nav";
import { useHaproxy } from "@/stores/haproxy";
import { useSession } from "@/stores/session";
import { closePanel } from "@/stores/ui";

const props = defineProps<{ namespace: string; name: string }>();
const s = useSession(), h = useHaproxy(), router = useRouter();
const list = ref<any[] | null>(null);
onMounted(async () => {
  if (!s.canGlobal("haproxy.view")) { list.value = []; return; }
  try { list.value = await hub.get(`/clusters/${s.current!.id}/services/${props.namespace}/${props.name}/haproxy`); } catch { list.value = []; }
});
function open(x: any) { h.select(x.haproxy_id); closePanel(); router.push({ path: to("haproxy-backends"), query: { focus: `b:${x.backend}` } }); }
</script>

<template>
  <div v-if="list?.length">
    <h3 class="section-title">Load balanced by HAProxy</h3>
    <div class="table-wrap card" style="box-shadow:none"><table class="data"><thead><tr><th>HAProxy · backend</th><th>Frontends</th><th>Servers</th><th class="num">Req/s</th><th class="num">Errors</th><th class="num">Resp.</th></tr></thead>
      <tbody><tr v-for="x in list" :key="`${x.haproxy_id}/${x.backend}`">
        <td class="primary"><button class="link" type="button" @click="open(x)">{{ x.instance }} · <span class="mono">{{ x.backend }}</span></button><div class="dim" style="font-size:12px">{{ x.via }}</div></td>
        <td>{{ x.frontends.join(", ") || "–" }}</td>
        <td><span v-for="sv in x.servers" :key="sv.name" class="sh-srv"><Badge :level="STATE[sv.state]?.level || 'neutral'" :label="sv.name" /></span></td>
        <td class="num">{{ fmtRate(x.live.rate) }}</td><td class="num" :style="{ color: x.live.error_pct >= 5 ? 'var(--critical)' : undefined }">{{ fmtRate(x.live.error_pct) }}%</td>
        <td class="num">{{ x.live.rtime_ms ? `${x.live.rtime_ms} ms` : "–" }}</td></tr></tbody></table></div>
  </div>
</template>

<style scoped>
.sh-srv { margin-right: 4px; }
</style>
