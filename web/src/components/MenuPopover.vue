<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { closeMenu, menu } from "@/stores/ui";
import Icon from "./Icon.vue";

const el = ref<HTMLElement | null>(null);
const pos = ref({ left: 0, top: 0 });
async function place() {
  if (!menu.anchor || !el.value) return;
  if (!menu.anchor.isConnected) { closeMenu(); return; }
  await nextTick();
  const r = menu.anchor.getBoundingClientRect(), mw = el.value.offsetWidth, mh = el.value.offsetHeight;
  const left = menu.anchor.dataset.menuAlign === "left" ? r.left : r.right - mw;
  pos.value = { left: Math.max(8, Math.min(left, innerWidth - mw - 8)), top: r.bottom + 4 + mh > innerHeight ? Math.max(8, r.top - mh - 4) : r.bottom + 4 };
}
watch(() => menu.anchor, async (a) => { if (a) { await place(); (el.value?.querySelector("button:not(:disabled)") as HTMLElement | null)?.focus({ preventScroll: true }); } });
function onDoc(e: MouseEvent) { if (menu.anchor && !el.value?.contains(e.target as Node) && !menu.anchor.contains(e.target as Node)) closeMenu(); }
function onKey(e: KeyboardEvent) {
  if (!menu.anchor) return;
  const items = [...(el.value?.querySelectorAll<HTMLElement>("button:not(:disabled)") || [])];
  const i = items.indexOf(document.activeElement as HTMLElement);
  if (e.key === "Escape" || e.key === "Tab") { const a = menu.anchor; closeMenu(); a.focus(); e.preventDefault(); }
  else if (e.key === "ArrowDown") { items[(i + 1) % items.length]?.focus(); e.preventDefault(); }
  else if (e.key === "ArrowUp") { items[(i - 1 + items.length) % items.length]?.focus(); e.preventDefault(); }
}
function run(i: number) { const it = menu.items[i]; closeMenu(); it.run?.(); }
onMounted(() => { document.addEventListener("click", onDoc, true); document.addEventListener("keydown", onKey); addEventListener("scroll", place, true); addEventListener("resize", closeMenu); });
onBeforeUnmount(() => { document.removeEventListener("click", onDoc, true); document.removeEventListener("keydown", onKey); removeEventListener("scroll", place, true); removeEventListener("resize", closeMenu); });
</script>
<template>
  <div ref="el" class="menu" :class="{ show: menu.anchor }" role="menu" :style="{ left: `${pos.left}px`, top: `${pos.top}px` }">
    <template v-for="(it, i) in menu.items" :key="i">
      <div v-if="it.sep" class="sep" />
      <div v-else-if="it.note" class="note"><Icon name="lock" />{{ it.note }}</div>
      <button v-else role="menuitem" :class="{ danger: it.danger }" :disabled="it.disabled" @click="run(i)"><Icon :name="it.icon || 'dot'" />{{ it.label }}</button>
    </template>
  </div>
</template>
