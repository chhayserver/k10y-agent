<script setup lang="ts">
import EmptyState from "@/components/EmptyState.vue";
import Icon from "@/components/Icon.vue";
import { fmt } from "@/lib/format";
defineProps<{ events: any[] }>();
</script>
<template>
  <ul v-if="events.length" class="list card" style="box-shadow:none">
    <li v-for="(e, i) in events" :key="i">
      <span class="ic" :class="{ warning: e.type === 'Warning' }"><Icon :name="e.type === 'Warning' ? 'warn' : 'dot'" /></span>
      <div style="min-width:0"><div class="main-line">{{ e.reason }}</div><div class="sub-line">{{ e.message }}</div></div>
      <span class="meta">{{ fmt.age(e.age_s) }} ago<template v-if="e.count > 1"> · ×{{ e.count }}</template></span>
    </li>
  </ul>
  <EmptyState v-else title="No events" sub="Kubernetes keeps events for about an hour." icon="ok" good />
</template>
