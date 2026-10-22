<script setup lang="ts">
defineProps<{ pct: number | null | undefined; mem?: boolean }>();
</script>
<template>
  <span v-if="pct == null" class="dim">–</span>
  <div v-else class="cell-meter"><div class="meter" :class="{ mem }"><span :style="{ width: `${Math.min(pct, 100)}%` }" /></div><span class="num">{{ Math.round(pct) }}%</span></div>
</template>
