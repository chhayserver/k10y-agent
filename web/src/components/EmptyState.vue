<script setup lang="ts">
import Icon from "./Icon.vue";
defineProps<{ title: string; sub?: string; icon?: string; good?: boolean }>();
</script>
<template>
  <div class="empty" :class="{ good }"><Icon :name="icon || 'search'" /><div class="title">{{ title }}</div><div v-if="sub">{{ sub }}</div><slot /></div>
</template>
