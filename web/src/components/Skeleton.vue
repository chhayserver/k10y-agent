<script setup lang="ts">
withDefaults(defineProps<{ rows?: number }>(), { rows: 4 });
</script>
<template><div style="padding:12px 18px;display:grid;gap:12px"><div v-for="i in rows" :key="i" class="skeleton" style="height:18px" /></div></template>
