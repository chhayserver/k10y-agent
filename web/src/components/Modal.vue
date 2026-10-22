<script setup lang="ts">
import { nextTick, ref, watch } from "vue";
const props = defineProps<{ open: boolean; title: string; wide?: boolean }>();
const emit = defineEmits<{ close: [] }>();
const dlg = ref<HTMLDialogElement | null>(null);
watch(() => props.open, async (o) => {
  await nextTick();
  if (o && dlg.value && !dlg.value.open) { dlg.value.showModal(); (dlg.value.querySelector("input, select, textarea") as HTMLElement | null)?.focus(); }
  if (!o && dlg.value?.open) dlg.value.close();
}, { immediate: true });
</script>
<template>
  <dialog ref="dlg" :class="{ wide }" @close="emit('close')">
    <form v-if="open" novalidate @submit.prevent>
      <h2>{{ title }}</h2>
      <slot />
      <div class="actions"><slot name="actions" /></div>
    </form>
  </dialog>
</template>
