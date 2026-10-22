<script setup lang="ts">
import { onBeforeUnmount, onMounted } from "vue";
import { closePanel, panel } from "@/stores/ui";
import ConfigPanel from "./ConfigPanel.vue";
import PodPanel from "./PodPanel.vue";
import ResourcePanel from "./ResourcePanel.vue";
function onKey(e: KeyboardEvent) { if (e.key === "Escape" && panel.current && !document.querySelector("dialog[open]") && !document.querySelector(".menu.show")) closePanel(); }
onMounted(() => document.addEventListener("keydown", onKey));
onBeforeUnmount(() => document.removeEventListener("keydown", onKey));
</script>
<template>
  <div class="backdrop" :class="{ show: panel.current }" @click="closePanel" />
  <aside class="drawer" :class="{ show: panel.current }" role="dialog" aria-modal="true" :aria-hidden="!panel.current">
    <template v-if="panel.current">
      <PodPanel v-if="panel.current.type === 'pod'" :key="`p/${panel.current.namespace}/${panel.current.name}`" :r="panel.current" />
      <ConfigPanel v-else-if="panel.current.type === 'config'" :key="`c/${panel.current.kind}/${panel.current.namespace}/${panel.current.name}`" :r="panel.current" />
      <ResourcePanel v-else :key="`r/${panel.current.kind}/${panel.current.namespace}/${panel.current.name}`" :r="panel.current" />
    </template>
  </aside>
</template>
