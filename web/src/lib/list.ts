import { computed, type ComputedRef } from "vue";
import type { PageState } from "@/stores/prefs";

/** Search + namespace (+ custom) filtering for a list page. */
export function useFiltered<T extends Record<string, any>>(rows: () => T[] | undefined, state: PageState, text: (r: T) => string,
  extra?: (r: T, filter: string) => boolean): { rows: ComputedRef<T[]>; all: ComputedRef<T[]>; count: ComputedRef<string> } {
  const all = computed(() => rows() || []);
  const filtered = computed(() => {
    const q = state.q.trim().toLowerCase();
    return all.value.filter((r) => (!state.ns || r.namespace === state.ns) && (!q || text(r).toLowerCase().includes(q)) && (!extra || extra(r, state.filter)));
  });
  return { rows: filtered, all, count: computed(() => `${filtered.value.length} of ${all.value.length}`) };
}
