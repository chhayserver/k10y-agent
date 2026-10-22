<script setup lang="ts">
import Badge from "./Badge.vue";
import Icon from "./Icon.vue";
import { deviceLabel } from "@/lib/device";
import { fmt } from "@/lib/format";
defineProps<{ sessions: any[] | null; canEnd?: boolean }>();
const emit = defineEmits<{ end: [s: any] }>();
const icon = (ua: string) => (/iPhone|Android|iPad/.test(ua) ? "pods" : "nodes");
</script>
<template>
  <div v-if="!sessions" class="hint">Loading…</div>
  <div v-else-if="!sessions.length" class="hint">No active sessions.</div>
  <ul v-else class="list card" style="box-shadow:none">
    <li v-for="x in sessions" :key="x.id"><span class="ic"><Icon :name="icon(x.user_agent)" /></span>
      <div style="min-width:0;flex:1"><div class="main-line">{{ deviceLabel(x.user_agent) }} <Badge v-if="x.current" level="good" label="This device" /></div>
        <div class="sub-line">{{ x.ip || "unknown IP" }} · signed in {{ fmt.ago(x.created_at) }} · active {{ fmt.ago(x.last_seen_at) }}</div></div>
      <button v-if="canEnd && !x.current" class="btn ghost sm" style="color:var(--critical)" @click="emit('end', x)">Sign out</button></li>
  </ul>
</template>
