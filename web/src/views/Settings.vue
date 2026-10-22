<script setup lang="ts">
import { computed } from "vue";
import Icon from "@/components/Icon.vue";
import { NAV } from "@/lib/nav";
import { applyTheme, prefs, resetPrefs } from "@/stores/prefs";
import { useSession } from "@/stores/session";
import { toast } from "@/stores/ui";
const s = useSession();
const groups = computed(() => {
  const out: { name: string; items: typeof NAV }[] = [];
  for (const n of NAV) { let g = out.find((x) => x.name === n.section); if (!g) out.push((g = { name: n.section, items: [] })); g.items.push(n); }
  return out;
});
function toggle(id: string, on: boolean) {
  prefs.hidden = on ? prefs.hidden.filter((x) => x !== id) : [...new Set([...prefs.hidden, id])];
  toast(true, `${NAV.find((n) => n.id === id)!.label} ${on ? "shown" : "hidden"}`, "prefs");
}
function theme(t: "system" | "light" | "dark") { prefs.theme = t; applyTheme(); }
function reset() { if (confirm("Reset all your dashboard preferences to their defaults?")) { resetPrefs(); applyTheme(); toast(true, "Preferences reset", "prefs"); } }
</script>
<template>
  <div class="settings-grid">
    <section class="card"><div class="card-head"><h2>Sidebar pages</h2><div class="right"><button class="btn sm" @click="prefs.hidden = []">Show all</button></div></div>
      <div class="card-body">
        <p class="hint" style="margin:0 0 12px">Choose which pages appear in your sidebar. Pages you don't have permission for are never shown.</p>
        <div class="tab-groups"><div v-for="g in groups" :key="g.name"><h4>{{ g.name }}</h4>
          <label v-for="n in g.items" :key="n.id" class="tab-toggle" :class="{ locked: n.locked }">
            <input type="checkbox" :checked="n.locked || !prefs.hidden.includes(n.id)" :disabled="n.locked" @change="toggle(n.id, ($event.target as HTMLInputElement).checked)">
            <Icon :name="n.icon" /><span>{{ n.label }}</span><span v-if="n.locked" class="hint">always shown</span></label></div></div>
      </div></section>
    <div style="display:flex;flex-direction:column;gap:16px">
      <section class="card"><div class="card-head"><h2>Preferences</h2></div><div class="card-body">
        <div class="pref-row"><div class="lbl">Theme<div>Follow your system, or force light or dark.</div></div>
          <div class="segmented" role="group"><button v-for="t in (['system', 'light', 'dark'] as const)" :key="t" :aria-pressed="prefs.theme === t" @click="theme(t)">{{ t[0].toUpperCase() + t.slice(1) }}</button></div></div>
        <div class="pref-row"><div class="lbl">Auto-refresh<div>How often cluster data reloads. Live charts update every 5 seconds.</div></div>
          <div class="segmented" role="group"><button v-for="v in [5, 10, 30, 60]" :key="v" :aria-pressed="prefs.refresh === v" @click="prefs.refresh = v">{{ v }}s</button></div></div>
        <div class="pref-row"><div class="lbl">Start page<div>The page that opens when you visit the dashboard.</div></div>
          <select v-model="prefs.start" class="select"><option v-for="n in NAV.filter((x) => !x.global && x.id !== 'settings')" :key="n.id" :value="n.id">{{ n.label }}</option></select></div>
      </div></section>
      <section class="card"><div class="card-head"><h2>Account</h2></div><div class="card-body">
        <div class="pref-row"><div class="lbl">{{ s.me?.user.display_name || s.me?.user.username }}<div>Signed in as {{ s.me?.user.username }}</div></div>
          <RouterLink class="btn" to="/password">Change password</RouterLink></div>
        <div class="pref-row"><div class="lbl">Reset preferences<div>Pages, theme, refresh, start page, table sorting and filters. Your account is not affected.</div></div>
          <button class="btn" @click="reset">Reset</button></div>
      </div></section>
      <p class="hint" style="margin:0">These preferences are saved in this browser only.</p>
    </div>
  </div>
</template>
