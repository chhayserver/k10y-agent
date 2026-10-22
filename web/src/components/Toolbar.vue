<script setup lang="ts">
import { computed } from "vue";
import type { PageState } from "@/stores/prefs";
import Icon from "./Icon.vue";
const props = defineProps<{ state: PageState; placeholder: string; namespaces?: string[]; filters?: [string, string][]; count?: string }>();
const nsList = computed(() => [...new Set(props.namespaces || [])].sort());
</script>
<template>
  <div class="toolbar">
    <label class="search"><Icon name="search" /><input v-model="state.q" class="input" :placeholder="placeholder" aria-label="Search"><kbd>/</kbd></label>
    <select v-if="namespaces" v-model="state.ns" class="select" aria-label="Namespace">
      <option value="">All namespaces</option><option v-for="n in nsList" :key="n" :value="n">{{ n }}</option></select>
    <div v-if="filters" class="segmented" role="group" aria-label="Filter">
      <button v-for="[v, l] in filters" :key="v" :aria-pressed="state.filter === v" @click="state.filter = v">{{ l }}</button></div>
    <slot />
    <span class="grow" /><span v-if="count" class="result-count">{{ count }}</span>
  </div>
</template>
