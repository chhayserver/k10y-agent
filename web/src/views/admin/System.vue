<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { hub } from "@/api/client";
import Badge from "@/components/Badge.vue";
import Icon from "@/components/Icon.vue";
import { toast } from "@/stores/ui";
const ai = ref<any>(null);
const drafts = reactive<Record<string, { key: string; model: string; base: string; models: string[]; busy: boolean; msg: string; ok: boolean }>>({});
async function load() {
  ai.value = await hub.get("/settings/ai");
  for (const [id, p] of Object.entries<any>(ai.value.providers)) drafts[id] = { key: "", model: p.model || p.default_model, base: p.base_url || p.default_base_url, models: drafts[id]?.models || [], busy: false, msg: "", ok: true };
}
onMounted(load);
async function save(id: string, activate: boolean) {
  const d = drafts[id]; d.busy = true; d.msg = "";
  try { ai.value = await hub.post("/settings/ai", { provider: id, api_key: d.key || null, model: d.model, base_url: d.base, activate }); d.key = ""; toast(true, `${ai.value.providers[id].label} saved`); await load(); }
  catch (e: any) { d.msg = e.message; d.ok = false; } finally { d.busy = false; }
}
async function models(id: string) {
  const d = drafts[id]; d.busy = true; d.msg = "";
  try { d.models = (await hub.post("/settings/ai/models", { provider: id, api_key: d.key || null, base_url: d.base }, 40000)).models; d.msg = `${d.models.length} models available.`; d.ok = true; }
  catch (e: any) { d.msg = e.message; d.ok = false; } finally { d.busy = false; }
}
async function activate(id: string) { try { ai.value = await hub.post("/settings/ai/active", { provider: id }); toast(true, `Now using ${ai.value.provider_label}`); } catch (e: any) { toast(false, e.message); } }
async function removeKey(id: string) { if (!confirm("Remove this provider's key?")) return; ai.value = await hub.del(`/settings/ai/${id}`); await load(); }
</script>
<template>
  <div class="section-bar"><h2>AI providers</h2><span class="hint">Keys are stored encrypted in the hub's PostgreSQL database and are never sent back to the browser. Every cluster uses the active provider.</span></div>
  <div v-if="ai" class="two-col">
    <section v-for="(p, id) in ai.providers" :key="id" class="card">
      <div class="card-head"><h2>{{ p.label }}</h2><div class="right">
        <Badge v-if="ai.provider === id && p.configured" level="good" label="Active" />
        <button v-else-if="p.configured" class="btn sm" @click="activate(String(id))">Use this provider</button>
        <Badge v-else level="neutral" label="No key" /></div></div>
      <div v-if="drafts[id]" class="card-body" style="display:flex;flex-direction:column;gap:12px">
        <div class="form-row"><label class="lbl">API key</label><input v-model="drafts[id].key" class="input" type="password" autocomplete="off" style="font-family:var(--mono)"
          :placeholder="p.key_hint ? `Saved key ${p.key_hint}. Leave empty to keep it` : id === 'gemini' ? 'Google API key' : 'cc_… key'">
          <span class="hint"><template v-if="id === 'gemini'">Create a key at aistudio.google.com/apikey.</template><template v-else>Create a key at codecraftapi.com. Any OpenAI-compatible API works with its base URL.</template></span></div>
        <div class="form-row"><label class="lbl">Model</label><div class="field"><input v-model="drafts[id].model" class="input" :list="`models-${id}`" style="font-family:var(--mono)" :placeholder="p.default_model || 'pick a model'">
          <button class="btn" type="button" :disabled="drafts[id].busy" @click="models(String(id))">Load models</button></div>
          <datalist :id="`models-${id}`"><option v-for="m in drafts[id].models" :key="m" :value="m" /></datalist>
          <span class="hint">The model must support tool (function) calling.</span></div>
        <div v-if="id !== 'gemini'" class="form-row"><label class="lbl">Base URL</label><input v-model="drafts[id].base" class="input" style="font-family:var(--mono)"></div>
        <div v-if="drafts[id].msg" class="test-result show" :class="drafts[id].ok ? 'ok' : 'err'"><Icon :name="drafts[id].ok ? 'ok' : 'crit'" /><span>{{ drafts[id].msg }}</span></div>
        <div style="display:flex;gap:8px">
          <button v-if="p.key_hint" class="btn ghost" style="color:var(--critical)" @click="removeKey(String(id))">Remove key</button><span style="flex:1" />
          <button class="btn" :disabled="drafts[id].busy" @click="save(String(id), false)">Save</button>
          <button class="btn primary" :disabled="drafts[id].busy" @click="save(String(id), true)">Save &amp; use</button>
        </div>
      </div>
    </section>
  </div>
  <p class="hint" style="margin-top:14px">Alert channels (Telegram) will appear here in the next stage.</p>
</template>
