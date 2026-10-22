// Personal preferences. Hidden pages, theme, refresh, start page and chart range are saved to the
// user's account in the hub (so they follow you to any device); table state stays in this browser.
import { reactive, watch } from "vue";
import { hub } from "@/api/client";

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

// --- account sync ---------------------------------------------------------------------
const SYNCED = ["hidden", "theme", "refresh", "start", "range"] as const;
type Synced = Pick<Prefs, (typeof SYNCED)[number]>;
const pick = (): Synced => Object.fromEntries(SYNCED.map((k) => [k, prefs[k]])) as Synced;
let syncing = false, timer = 0, lastSent = "";

/** After login: take the account's saved preferences, or save this browser's ones if the account has none yet. */
export async function loadAccountPrefs() {
  syncing = false;
  try {
    const { data } = await hub.get<{ data: Partial<Synced> | null }>("/auth/preferences");
    if (data) {
      for (const k of SYNCED) if (data[k] !== undefined) (prefs as any)[k] = data[k];
      lastSent = JSON.stringify(pick());
    } else {
      lastSent = "";
      await saveAccountPrefs();
    }
    syncing = true;
  } catch { /* offline or older hub: keep local preferences */ }
}

async function saveAccountPrefs() {
  const body = JSON.stringify(pick());
  if (body === lastSent) return;
  try { await hub.put("/auth/preferences", pick()); lastSent = body; } catch { /* retried on next change */ }
}

export function stopAccountSync() { syncing = false; clearTimeout(timer); }

watch(() => SYNCED.map((k) => JSON.stringify(prefs[k])).join("|"), () => {
  if (!syncing) return;
  clearTimeout(timer);
  timer = window.setTimeout(saveAccountPrefs, 800);
});
