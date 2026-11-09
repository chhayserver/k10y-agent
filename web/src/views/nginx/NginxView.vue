<script setup lang="ts">
// Nginx: sites with their routes, upstreams with live server health, live numbers from the access log, logs,
// certificates and the raw config. One component for every Nginx page (`tab`).
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import EmptyState from "@/components/EmptyState.vue";
import Icon from "@/components/Icon.vue";
import Skeleton from "@/components/Skeleton.vue";
import { to } from "@/lib/nav";
import { useNginx } from "@/stores/nginx";
import { useSession } from "@/stores/session";
import NgCertificates from "./NgCertificates.vue";
import NgConfig from "./NgConfig.vue";
import NgLogs from "./NgLogs.vue";
import NgOverview from "./NgOverview.vue";
import NgSites from "./NgSites.vue";
import NgUpstreams from "./NgUpstreams.vue";

type Tab = "overview" | "sites" | "upstreams" | "logs" | "certificates" | "config";
defineProps<{ tab: Tab }>();
const s = useSession(), n = useNginx();
const canAdd = computed(() => s.canAll("nginx.manage"));
const focus = ref("");
let timer = 0;
onMounted(async () => {
  if (n.instances == null) await n.loadInstances(); else if (!n.data) n.refresh();
  timer = window.setInterval(() => { if (!n.paused && !document.hidden) n.refresh(); }, 5000);
});
onBeforeUnmount(() => clearInterval(timer));
</script>

<template>
  <section v-if="n.instances && !n.instances.length" class="card"><div class="card-body">
    <EmptyState title="No Nginx connected yet" icon="nginx"
      :sub="canAdd ? 'Run the k10y agent next to Nginx (on the server, or the k10y/k10y-nginx image in Docker Compose), then add it under Administration → Nginx instances.' : 'Ask an administrator to connect an Nginx and give you a role on it.'" />
    <div v-if="canAdd" style="display:flex;justify-content:center;gap:8px;margin-top:12px">
      <RouterLink class="btn primary" :to="to('manage-nginx')"><Icon name="nginx" /><span>Add Nginx</span></RouterLink>
      <a class="btn" href="https://k10y-agents.pages.dev/docs/nginx" target="_blank" rel="noopener">How to install the agent<Icon name="external" /></a></div>
  </div></section>
  <div v-else-if="n.error && !n.data" class="banner show"><Icon name="crit" /><span class="msg">{{ n.error }}</span></div>
  <Skeleton v-else-if="!n.data" :rows="6" />
  <template v-else>
    <div v-if="n.error" class="banner show"><Icon name="warn" /><span class="msg">Couldn't refresh: {{ n.error }}</span></div>
    <NgOverview v-if="tab === 'overview'" @focus="(k) => (focus = k)" />
    <NgSites v-else-if="tab === 'sites'" :focus="focus" />
    <NgUpstreams v-else-if="tab === 'upstreams'" :focus="focus" />
    <NgLogs v-else-if="tab === 'logs'" />
    <NgCertificates v-else-if="tab === 'certificates'" />
    <NgConfig v-else-if="tab === 'config'" />
  </template>
</template>
