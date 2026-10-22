<script setup lang="ts" generic="T extends Record<string, any>">
import { computed } from "vue";
import type { SortState } from "@/stores/prefs";
import EmptyState from "./EmptyState.vue";
import Icon from "./Icon.vue";

export interface Column<R> { key: string; label: string; num?: boolean; cls?: string; nosort?: boolean; sort?: (r: R) => unknown }
const props = defineProps<{ rows: T[]; columns: Column<T>[]; clickable?: boolean; emptyTitle?: string; emptySub?: string; emptyIcon?: string; emptyGood?: boolean; rowKey?: (r: T) => string }>();
const sort = defineModel<SortState>("sort", { required: true });
const emit = defineEmits<{ row: [row: T] }>();

const sorted = computed(() => {
  const col = props.columns.find((c) => c.key === sort.value.key) || props.columns[0];
  const val = (r: T) => (col.sort ? col.sort(r) : r[col.key]);
  return [...props.rows].sort((a, b) => {
    const va = val(a) as any, vb = val(b) as any;
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    const c = typeof va === "number" && typeof vb === "number" ? va - vb : String(va).localeCompare(String(vb), undefined, { numeric: true });
    return c * sort.value.dir;
  });
});
function toggle(key: string) { sort.value = key === sort.value.key ? { key, dir: sort.value.dir === 1 ? -1 : 1 } : { key, dir: 1 }; }
function onRow(e: Event, r: T) { if (props.clickable && !(e.target as HTMLElement).closest("button, a, input, select")) emit("row", r); }
function onKey(e: KeyboardEvent, r: T) { if (props.clickable && e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); emit("row", r); } }
const key = (r: T, i: number) => (props.rowKey ? props.rowKey(r) : `${r.namespace ?? ""}/${r.name ?? r.id ?? i}`);
</script>

<template>
  <EmptyState v-if="!rows.length" :title="emptyTitle || 'Nothing to show'" :sub="emptySub" :icon="emptyIcon" :good="emptyGood" />
  <table v-else class="data">
    <thead>
      <tr>
        <th v-for="c in columns" :key="c.key" scope="col" :class="[c.num ? 'num' : '', c.cls === 'actions' ? 'actions' : '']"
            :aria-sort="c.key === sort.key ? (sort.dir === 1 ? 'ascending' : 'descending') : undefined">
          <span v-if="c.nosort" :class="c.cls === 'actions' ? 'sr-only' : 'th-btn'" style="cursor:default">{{ c.label }}</span>
          <button v-else class="th-btn" @click="toggle(c.key)">{{ c.label }}<Icon name="sort" /></button>
        </th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="(r, i) in sorted" :key="key(r, i)" :class="{ clickable }" :tabindex="clickable ? 0 : undefined"
          @click="onRow($event, r)" @keydown="onKey($event, r)">
        <td v-for="c in columns" :key="c.key" :class="[c.num ? 'num' : '', c.cls || '']">
          <slot :name="c.key" :row="r">{{ r[c.key] ?? "–" }}</slot>
        </td>
      </tr>
    </tbody>
  </table>
</template>
