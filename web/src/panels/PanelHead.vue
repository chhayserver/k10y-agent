<script setup lang="ts">
import Icon from "@/components/Icon.vue";
import { KIND_ICON } from "@/lib/format";
import { closePanel, panel, panelBack } from "@/stores/ui";
defineProps<{ kind: string; name: string; namespace?: string }>();
</script>
<template>
  <div class="drawer-head">
    <button v-if="panel.stack.length" class="btn icon-only ghost" aria-label="Back" title="Back" @click="panelBack"><Icon name="rollback" /></button>
    <div style="min-width:0;flex:1">
      <div style="display:flex;align-items:center;gap:6px;color:var(--muted);font-size:12px;font-weight:550;text-transform:uppercase;letter-spacing:.05em"><Icon :name="KIND_ICON[kind] || 'dot'" />{{ kind }}</div>
      <h2 id="drawer-title">{{ name }}</h2>
      <div class="sub"><span v-if="namespace" class="ns">{{ namespace }}</span><slot name="status" /></div>
    </div>
    <slot name="actions" />
    <button class="btn icon-only ghost" aria-label="Close (Esc)" title="Close (Esc)" @click="closePanel"><Icon name="x" /></button>
  </div>
</template>
