// Per-browser preferences (hidden pages, theme, refresh, start page, table state).
import { reactive, watch } from "vue";

export interface SortState { key: string; dir: 1 | -1 }
export interface PageState { q: string; ns: string; filter: string; sort: SortState }

interface Prefs {
  hidden: string[];
  theme: "system" | "light" | "dark";
  refresh: number;
  start: string;
  range: "5m" | "15m" | "1h";
  pages: Record<string, PageState>;
  map: { ns: string; rs: boolean; config: boolean; q: string };
}

const KEY = "k10y-prefs";
const defaults = (): Prefs => ({
  hidden: [], theme: "system", refresh: 10, start: "overview", range: "15m", pages: {},
  map: { ns: "", rs: false, config: true, q: "" },
});

function load(): Prefs {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || "null");
    return raw ? { ...defaults(), ...raw, map: { ...defaults().map, ...(raw.map || {}) } } : defaults();
  } catch { return defaults(); }
}

export const prefs = reactive<Prefs>(load());
watch(prefs, (v) => { try { localStorage.setItem(KEY, JSON.stringify(v)); } catch { /* private mode */ } }, { deep: true });

export function page(id: string, sort: SortState = { key: "namespace", dir: 1 }, filter = "all"): PageState {
  if (!prefs.pages[id]) prefs.pages[id] = { q: "", ns: "", filter, sort };
  return prefs.pages[id];
}

export function resetPrefs() {
  Object.assign(prefs, defaults());
}

export function applyTheme() {
  const root = document.documentElement;
  if (prefs.theme === "system") delete root.dataset.theme;
  else root.dataset.theme = prefs.theme;
}
watch(() => prefs.theme, applyTheme);

export const isDark = () => prefs.theme === "dark" || (prefs.theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
