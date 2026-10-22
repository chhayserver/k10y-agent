<script setup lang="ts">
defineProps<{ items?: Record<string, string> | null; keys?: string[]; max?: number }>();
</script>
<template>
  <span v-if="items && Object.keys(items).length"><span v-for="(v, k) in items" :key="k" class="chip">{{ k }}={{ v }}</span></span>
  <span v-else-if="keys && keys.length"><span v-for="k in keys.slice(0, max || 99)" :key="k" class="chip">{{ k }}</span><span v-if="max && keys.length > max" class="dim">+{{ keys.length - max }}</span></span>
  <span v-else class="dim">–</span>
</template>
