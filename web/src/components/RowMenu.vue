<script setup lang="ts">
import { openMenu, type MenuItem } from "@/stores/ui";
import Icon from "./Icon.vue";
const props = defineProps<{ items: () => MenuItem[]; label?: string }>();
function open(e: MouseEvent) { e.stopPropagation(); openMenu(e.currentTarget as HTMLElement, props.items()); }
</script>
<template>
  <button class="btn icon-only ghost menu-btn" aria-haspopup="menu" :aria-label="`Actions for ${label || ''}`" title="Actions" @click="open"><Icon name="more" /></button>
</template>
