<script setup lang="ts">
import { computed } from "vue";
import Icon from "./Icon.vue";
const props = defineProps<{ level: string; label: string | number; lg?: boolean }>();
const ICON: Record<string, string> = { good: "ok", warning: "warn", serious: "warn", critical: "crit", neutral: "dot", pending: "pending" };
const cls = computed(() => (props.level === "pending" ? "warning" : props.level));
</script>
<template><span class="badge" :class="[cls, { lg }]"><Icon :name="ICON[level] || 'dot'" />{{ label }}</span></template>
