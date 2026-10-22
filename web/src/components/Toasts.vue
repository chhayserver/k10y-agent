<script setup lang="ts">
import { toasts } from "@/stores/ui";
import Icon from "./Icon.vue";
</script>
<template>
  <div class="toasts" aria-live="polite">
    <div v-for="t in toasts" :key="t.id" class="toast">
      <span :class="t.ok ? 'ic-ok' : 'ic-err'"><Icon :name="t.ok ? 'ok' : 'crit'" /></span>
      <span class="msg">{{ t.message }}</span>
      <button class="btn icon-only ghost sm" aria-label="Dismiss" @click="toasts.splice(toasts.indexOf(t), 1)"><Icon name="x" /></button>
    </div>
  </div>
</template>
