<script setup lang="ts">
defineProps<{ model: Record<string, any> }>();
function tab(e: KeyboardEvent) {
  if (e.key !== "Tab") return;
  e.preventDefault();
  const ta = e.target as HTMLTextAreaElement, p = ta.selectionStart;
  ta.setRangeText("  ", p, ta.selectionEnd, "end");
  ta.dispatchEvent(new Event("input"));
}
</script>
<template>
  <textarea v-model="model.yaml" class="yaml" spellcheck="false" aria-label="YAML manifest" :placeholder="model.placeholder" @keydown="tab" />
  <span class="hint">Tip: run <b>Dry run</b> first. The server validates everything without changing the cluster.</span>
</template>
