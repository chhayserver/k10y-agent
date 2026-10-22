<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { hub } from "@/api/client";
import Badge from "@/components/Badge.vue";
import Icon from "@/components/Icon.vue";
import { toast } from "@/stores/ui";
import Modal from "@/components/Modal.vue";
const ai = ref<any>(null);
const drafts = reactive<Record<string, { key: string; model: string; base: string; models: string[]; busy: boolean; msg: string; ok: boolean }>>({});
async function load() {
  ai.value = await hub.get("/settings/ai");
  for (const [id, p] of Object.entries<any>(ai.value.providers)) drafts[id] = { key: "", model: p.model || p.default_model, base: p.base_url || p.default_base_url, models: drafts[id]?.models || [], busy: false, msg: "", ok: true };
}
onMounted(load);

// alert channels (Telegram)
const channels = ref<any[] | null>(null);
async function loadChannels() { channels.value = await hub.get("/alerts/channels"); }
onMounted(loadChannels);
const ch = reactive({ open: false, id: 0, name: "", chat_id: "", bot_token: "", enabled: true, token_set: false, chats: [] as any[], busy: false, msg: "", ok: true });
function newChannel() { Object.assign(ch, { open: true, id: 0, name: "Telegram", chat_id: "", bot_token: "", enabled: true, token_set: false, chats: [], msg: "" }); }
function editChannel(c: any) { Object.assign(ch, { open: true, id: c.id, name: c.name, chat_id: c.chat_id, bot_token: "", enabled: c.enabled, token_set: c.token_set, chats: [], msg: "" }); }
async function findChats() {
  ch.busy = true; ch.msg = "";
  try {
    ch.chats = (await hub.post("/alerts/telegram/chats", { bot_token: ch.bot_token || null, channel_id: ch.id || null })).chats;
    if (ch.chats.length === 1) ch.chat_id = ch.chats[0].id;
    ch.ok = !!ch.chats.length; ch.msg = ch.chats.length ? `Found ${ch.chats.length} chat(s).` : "No chats yet. Send any message to the bot (or add it to a group and post there), then try again.";
  } catch (e: any) { ch.ok = false; ch.msg = e.message; } finally { ch.busy = false; }
}
async function saveChannel() {
  ch.busy = true; ch.msg = "";
  const body = { name: ch.name, type: "telegram", chat_id: ch.chat_id, bot_token: ch.bot_token || null, enabled: ch.enabled };
  try {
    const saved = ch.id ? await hub.patch(`/alerts/channels/${ch.id}`, body) : await hub.post("/alerts/channels", body);
    Object.assign(ch, { id: saved.id, bot_token: "", token_set: true });
    await hub.post(`/alerts/channels/${saved.id}/test`, {});
    ch.open = false; toast(true, `Saved ${saved.name}. A test message was sent.`); await loadChannels();
  } catch (e: any) { ch.ok = false; ch.msg = e.message; await loadChannels(); } finally { ch.busy = false; }
}
async function testChannel(c: any) { try { await hub.post(`/alerts/channels/${c.id}/test`, {}); toast(true, `Test message sent to ${c.name}`); } catch (e: any) { toast(false, e.message); } }
async function removeChannel(c: any) {
  if (!confirm(`Delete channel ${c.name}? Rules that use it stop sending there.`)) return;
  await hub.del(`/alerts/channels/${c.id}`); await loadChannels();
}
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

  <div class="section-bar" style="margin-top:28px"><h2>Alert channels</h2><span class="hint">Where alert rules send messages. Bot tokens are stored encrypted in PostgreSQL.</span>
    <span class="grow" /><button class="btn primary" @click="newChannel"><Icon name="bell" /><span>Add Telegram bot</span></button></div>
  <section class="card flush"><div class="card-body">
    <div v-if="channels && !channels.length" class="empty"><Icon name="bell" /><div class="title">No channels yet</div>
      <div class="s">Create a bot with <b>@BotFather</b> in Telegram, then add its token here.</div></div>
    <table v-else-if="channels" class="data"><thead><tr><th>Name</th><th>Type</th><th>Chat ID</th><th>Status</th><th class="actions"><span class="sr-only">Actions</span></th></tr></thead>
      <tbody><tr v-for="c in channels" :key="c.id" class="clickable" @click="(e) => !(e.target as HTMLElement).closest('button') && editChannel(c)">
        <td class="primary">{{ c.name }}</td><td>Telegram</td><td class="mono">{{ c.chat_id }}</td>
        <td><Badge :level="c.enabled ? 'good' : 'neutral'" :label="c.enabled ? 'Enabled' : 'Off'" /></td>
        <td class="actions"><button class="btn ghost sm" @click="testChannel(c)">Send test</button><button class="btn ghost sm" style="color:var(--critical)" @click="removeChannel(c)">Delete</button></td></tr></tbody></table>
  </div></section>

  <Modal :open="ch.open" :title="ch.id ? `Edit ${ch.name}` : 'Add Telegram bot'" @close="ch.open = false">
    <ol class="hint" style="margin:0;padding-left:18px;line-height:1.6"><li>In Telegram, message <b>@BotFather</b>, send <code>/newbot</code> and copy the token.</li>
      <li>Send any message to your new bot, or add it to a group and post there.</li><li>Paste the token and press <b>Find chats</b>.</li></ol>
    <div class="form-row"><label class="lbl">Name</label><input v-model="ch.name" class="input" placeholder="e.g. Ops group"></div>
    <div class="form-row"><label class="lbl">Bot token</label><input v-model="ch.bot_token" class="input" type="password" autocomplete="off" style="font-family:var(--mono)"
      :placeholder="ch.token_set ? 'Saved. Leave empty to keep it' : '123456789:AA…'"></div>
    <div class="form-row"><label class="lbl">Chat ID</label><div class="field"><input v-model="ch.chat_id" class="input" style="font-family:var(--mono)" placeholder="e.g. 123456789 or -100…">
      <button class="btn" type="button" :disabled="ch.busy || (!ch.bot_token && !ch.token_set)" @click="findChats">Find chats</button></div>
      <div v-if="ch.chats.length" style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px">
        <button v-for="c in ch.chats" :key="c.id" type="button" class="pill" :aria-pressed="ch.chat_id === c.id" @click="ch.chat_id = c.id">{{ c.title || c.id }} <span class="dim">{{ c.type }}</span></button></div></div>
    <label class="pill" style="align-self:flex-start"><input v-model="ch.enabled" type="checkbox"> Enabled</label>
    <div v-if="ch.msg" class="test-result show" :class="ch.ok ? 'ok' : 'err'"><Icon :name="ch.ok ? 'ok' : 'crit'" /><span>{{ ch.msg }}</span></div>
    <template #actions><button class="btn ghost" type="button" @click="ch.open = false">Cancel</button><span style="flex:1" />
      <button class="btn primary" :disabled="ch.busy || !ch.name || !ch.chat_id || (!ch.bot_token && !ch.token_set)" @click="saveChannel">Save &amp; send test</button></template>
  </Modal>
</template>
