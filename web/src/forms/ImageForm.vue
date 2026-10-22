<script setup lang="ts">
import { computed, watch } from "vue";
const props = defineProps<{ model: Record<string, any> }>();
const containers = computed<{ name: string; image: string }[]>(() => props.model.containers || []);
const current = computed(() => containers.value.find((c) => c.name === props.model.container)?.image || "");
watch(() => props.model.container, () => { props.model.image = current.value; });
</script>
<template>
  <div v-if="containers.length > 1" class="form-row">
    <label class="lbl" for="img-c">Container</label>
    <select id="img-c" v-model="model.container" class="select"><option v-for="c in containers" :key="c.name">{{ c.name }}</option></select>
  </div>
  <div class="form-row">
    <label class="lbl" for="img-v">Image</label>
    <input id="img-v" v-model="model.image" class="input" spellcheck="false" style="font-family:var(--mono)">
    <span class="hint">Current: {{ current }}</span>
  </div>
</template>
