<script setup lang="ts">
import PasswordInput from "@/components/PasswordInput.vue";
import { onMounted, reactive, ref } from "vue";
import { hub } from "@/api/client";
import Badge from "@/components/Badge.vue";
import Icon from "@/components/Icon.vue";
import { toast } from "@/stores/ui";
import Modal from "@/components/Modal.vue";
import { useSession } from "@/stores/session";
import ProtectedNamespaces from "./ProtectedNamespaces.vue";
import SsoSettings from "./SsoSettings.vue";
const session = useSession();
// AI providers and alert channels need settings.manage; protected namespaces need namespaces.protect on a cluster.
const manage = session.canAll("settings.manage"), protect = session.canGlobal("namespaces.protect");
const ai = ref<any>(null);

// sign-in security: require two-factor login
const security = reactive({ require_2fa: "off", saved: "off", busy: false, msg: "" });
onMounted(async () => { if (!manage) return; try { const r = await hub.get<any>("/settings/security"); security.require_2fa = security.saved = r.require_2fa; } catch { /* older hub */ } });
async function saveSecurity() {
  security.busy = true; security.msg = "";
  try { security.saved = (await hub.put<any>("/settings/security", { require_2fa: security.require_2fa })).require_2fa; toast(true, "Sign-in security saved"); }
  catch (e: any) { security.msg = e.message; } finally { security.busy = false; }
}
const drafts = reactive<Record<string, { key: string; model: string; base: string; models: string[]; busy: boolean; msg: string; ok: boolean }>>({});
async function load() {
  ai.value = await hub.get("/settings/ai");
  for (const [id, p] of Object.entries<any>(ai.value.providers)) drafts[id] = { key: "", model: p.model || p.default_model, base: p.base_url || p.default_base_url, models: drafts[id]?.models || [], busy: false, msg: "", ok: true };
}
onMounted(() => manage && load());

// alert channels (Telegram)
const channels = ref<any[] | null>(null);
async function loadChannels() { channels.value = await hub.get("/alerts/channels"); }
onMounted(() => manage && loadChannels());
type ChType = "telegram" | "slack" | "email" | "webhook";
const CH_TYPES: { id: ChType; label: string; hint: string }[] = [
  { id: "telegram", label: "Telegram", hint: "A bot posts to a chat or group." },
  { id: "slack", label: "Slack", hint: "An incoming webhook posts to a channel." },
  { id: "email", label: "Email", hint: "Sent through your SMTP server." },
  { id: "webhook", label: "Webhook", hint: "JSON POSTed to your URL (PagerDuty, Teams, your own service…)." },
];
const CH_LABEL = Object.fromEntries(CH_TYPES.map((t) => [t.id, t.label])) as Record<string, string>;
const blank = { id: 0, type: "telegram" as ChType, name: "Telegram", chat_id: "", bot_token: "", secret: "", url: "", host: "", port: 587, security: "starttls",
  username: "", sender: "", recipients: "", enabled: true, token_set: false, savedType: "" };
const ch = reactive({ open: false, ...blank, chats: [] as any[], busy: false, msg: "", ok: true });
function newChannel() { Object.assign(ch, { open: true, ...blank, chats: [], msg: "" }); }
function editChannel(c: any) {
  Object.assign(ch, { open: true, ...blank, id: c.id, type: c.type, savedType: c.type, name: c.name, chat_id: c.chat_id || "", url: c.url || "", host: c.host || "",
    port: c.port || 587, security: c.security || "starttls", username: c.username || "", sender: c.sender || "", recipients: (c.recipients || []).join(", "),
    enabled: c.enabled, token_set: c.token_set, chats: [], msg: "" });
}
function pickType(t: ChType) { if (!ch.id && (!ch.name || CH_TYPES.some((x) => x.label === ch.name))) ch.name = CH_LABEL[t]; ch.type = t; ch.msg = ""; }
// A stored secret only counts while the type stays the same.
const hasSecret = () => ch.token_set && ch.savedType === ch.type;
const canSave = () => !!ch.name && (ch.type === "telegram" ? !!ch.chat_id && (!!ch.bot_token || hasSecret())
  : ch.type === "slack" ? !!ch.secret || hasSecret() : ch.type === "webhook" ? /^https?:\/\//.test(ch.url)
  : !!ch.host && !!ch.sender && !!ch.recipients.trim());
const describeChannel = (c: any) => c.type === "telegram" ? c.chat_id : c.type === "email" ? (c.recipients || []).join(", ") : c.type === "webhook" ? c.url : "incoming webhook";
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
  const body = { name: ch.name, type: ch.type, enabled: ch.enabled, chat_id: ch.chat_id, bot_token: ch.bot_token || null, secret: ch.secret || null,
    url: ch.url, host: ch.host, port: Number(ch.port) || 587, security: ch.security, username: ch.username, sender: ch.sender,
    recipients: ch.recipients.split(/[\s,;]+/).filter(Boolean) };
  try {
    const saved = ch.id ? await hub.patch(`/alerts/channels/${ch.id}`, body) : await hub.post("/alerts/channels", body);
    Object.assign(ch, { id: saved.id, bot_token: "", secret: "", token_set: saved.secret_set, savedType: saved.type });
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
  <template v-if="protect"><ProtectedNamespaces /><div style="height:28px" /></template>
  <template v-if="manage">
  <div class="section-bar"><h2>Sign-in security</h2><span class="hint">Two-factor login adds a code from an authenticator app to every sign-in. Each person sets it up in My settings.</span></div>
  <section class="card" style="margin-bottom:28px"><div class="card-body" style="display:flex;flex-direction:column;gap:12px">
    <div class="pref-row"><div class="lbl">Require two-factor login<div>People who must use it and haven't set it up are asked to do it right after signing in. Admins are users with a role on everything that can manage users, settings, clusters or HAProxy instances.</div></div>
      <div class="segmented" role="group"><button v-for="[v, l] in [['off', 'Optional'], ['admins', 'Admins'], ['everyone', 'Everyone']]" :key="v" type="button" :aria-pressed="security.require_2fa === v" @click="security.require_2fa = v">{{ l }}</button></div></div>
    <div v-if="security.msg" class="test-result show err"><Icon name="crit" /><span>{{ security.msg }}</span></div>
    <div v-if="security.require_2fa !== security.saved" style="display:flex;gap:8px;justify-content:flex-end"><button class="btn ghost" type="button" @click="security.require_2fa = security.saved">Undo</button>
      <button class="btn primary" type="button" :disabled="security.busy" @click="saveSecurity">Save</button></div>
  </div></section>
  <SsoSettings />
  <div class="section-bar"><h2>AI providers</h2><span class="hint">Keys are stored encrypted in the hub's PostgreSQL database and are never sent back to the browser. Every cluster uses the active provider.</span></div>
  <div v-if="ai" class="two-col">
    <section v-for="(p, id) in ai.providers" :key="id" class="card">
      <div class="card-head"><h2>{{ p.label }}</h2><div class="right">
        <Badge v-if="ai.provider === id && p.configured" level="good" label="Active" />
        <button v-else-if="p.configured" class="btn sm" @click="activate(String(id))">Use this provider</button>
        <Badge v-else level="neutral" label="No key" /></div></div>
      <div v-if="drafts[id]" class="card-body" style="display:flex;flex-direction:column;gap:12px">
        <div class="form-row"><label class="lbl">API key</label><PasswordInput v-model="drafts[id].key" autocomplete="off" mono
          :placeholder="p.key_hint ? `Saved key ${p.key_hint}. Leave empty to keep it` : ({ gemini: 'Google API key', openrouter: 'sk-or-… key' } as Record<string, string>)[id] || 'cc_… key'" />
          <span class="hint"><template v-if="id === 'gemini'">Create a key at aistudio.google.com/apikey.</template><template v-else-if="id === 'openrouter'">Create a key at openrouter.ai/keys. Model names look like <code>vendor/model</code>; <b>Load models</b> lists only those that support tool calling.</template><template v-else>Create a key at codecraftapi.com. Any OpenAI-compatible API works with its base URL.</template></span></div>
        <div class="form-row"><label class="lbl">Model</label><div class="field"><input v-model="drafts[id].model" class="input" :list="`models-${id}`" style="font-family:var(--mono)" :placeholder="p.default_model || (id === 'openrouter' ? 'e.g. google/… or openai/… (Load models)' : 'pick a model')">
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

  <div class="section-bar" style="margin-top:28px"><h2>Alert channels</h2><span class="hint">Where alert rules send messages: Telegram, Slack, email or a webhook. Tokens, webhook URLs and passwords are stored encrypted in PostgreSQL.</span>
    <span class="grow" /><button class="btn primary" @click="newChannel"><Icon name="bell" /><span>Add channel</span></button></div>
  <section class="card flush"><div class="card-body">
    <div v-if="channels && !channels.length" class="empty"><Icon name="bell" /><div class="title">No channels yet</div>
      <div class="s">Add a Telegram bot, a Slack incoming webhook, an email address or your own webhook.</div></div>
    <table v-else-if="channels" class="data"><thead><tr><th>Name</th><th>Type</th><th>Sends to</th><th>Status</th><th class="actions"><span class="sr-only">Actions</span></th></tr></thead>
      <tbody><tr v-for="c in channels" :key="c.id" class="clickable" @click="(e) => !(e.target as HTMLElement).closest('button') && editChannel(c)">
        <td class="primary">{{ c.name }}</td><td>{{ CH_LABEL[c.type] || c.type }}</td><td class="mono" style="max-width:320px;overflow:hidden;text-overflow:ellipsis">{{ describeChannel(c) }}</td>
        <td><Badge :level="c.enabled ? 'good' : 'neutral'" :label="c.enabled ? 'Enabled' : 'Off'" /></td>
        <td class="actions"><button class="btn ghost sm" @click="testChannel(c)">Send test</button><button class="btn ghost sm" style="color:var(--critical)" @click="removeChannel(c)">Delete</button></td></tr></tbody></table>
  </div></section>
  </template>

  <Modal :open="ch.open" :title="ch.id ? `Edit ${ch.name}` : 'Add notification channel'" @close="ch.open = false">
    <div class="form-row"><span class="lbl">Type</span><div class="segmented" role="group">
      <button v-for="t in CH_TYPES" :key="t.id" type="button" :aria-pressed="ch.type === t.id" @click="pickType(t.id)">{{ t.label }}</button></div>
      <span class="hint">{{ CH_TYPES.find((t) => t.id === ch.type)?.hint }}</span></div>
    <div class="form-row"><label class="lbl">Name</label><input v-model="ch.name" class="input" placeholder="e.g. Ops group"></div>
    <template v-if="ch.type === 'telegram'">
      <ol class="hint" style="margin:0;padding-left:18px;line-height:1.6"><li>In Telegram, message <b>@BotFather</b>, send <code>/newbot</code> and copy the token.</li>
        <li>Send any message to your new bot, or add it to a group and post there.</li><li>Paste the token and press <b>Find chats</b>.</li></ol>
      <div class="form-row"><label class="lbl">Bot token</label><PasswordInput v-model="ch.bot_token" autocomplete="off" mono
        :placeholder="hasSecret() ? 'Saved. Leave empty to keep it' : '123456789:AA…'" /></div>
      <div class="form-row"><label class="lbl">Chat ID</label><div class="field"><input v-model="ch.chat_id" class="input" style="font-family:var(--mono)" placeholder="e.g. 123456789 or -100…">
        <button class="btn" type="button" :disabled="ch.busy || (!ch.bot_token && !hasSecret())" @click="findChats">Find chats</button></div>
        <div v-if="ch.chats.length" style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px">
          <button v-for="c in ch.chats" :key="c.id" type="button" class="pill" :aria-pressed="ch.chat_id === c.id" @click="ch.chat_id = c.id">{{ c.title || c.id }} <span class="dim">{{ c.type }}</span></button></div></div>
    </template>
    <template v-else-if="ch.type === 'slack'">
      <p class="hint" style="margin:0">In Slack: <b>Apps → Incoming Webhooks → Add to Slack</b>, pick a channel and copy the webhook URL.</p>
      <div class="form-row"><label class="lbl">Webhook URL</label><PasswordInput v-model="ch.secret" autocomplete="off" mono
        :placeholder="hasSecret() ? 'Saved. Leave empty to keep it' : 'https://hooks.slack.com/services/…'" /></div>
    </template>
    <template v-else-if="ch.type === 'webhook'">
      <div class="form-row"><label class="lbl">URL</label><input v-model="ch.url" class="input" style="font-family:var(--mono)" placeholder="https://ops.example.com/k10y-alerts"></div>
      <div class="form-row"><label class="lbl">Signing secret (optional)</label><PasswordInput v-model="ch.secret" autocomplete="off" mono
        :placeholder="hasSecret() ? 'Saved. Leave empty to keep it' : 'any long random string'" />
        <span class="hint">With a secret, each request has <code>X-K10y-Signature: sha256=…</code>, the HMAC-SHA256 of the body, so your service can check it came from k10y. The body is JSON: status (firing, resolved, reminder, acknowledged, test), rule, severity, title, cluster, resource and text.</span></div>
    </template>
    <template v-else>
      <div class="form-row"><label class="lbl">SMTP server</label><div class="field"><input v-model="ch.host" class="input" placeholder="smtp.example.com" style="flex:2">
        <input v-model.number="ch.port" class="input" type="number" min="1" max="65535" aria-label="Port" style="max-width:100px">
        <select v-model="ch.security" class="select" aria-label="Encryption" style="max-width:150px" @change="ch.port = ch.security === 'ssl' ? 465 : ch.security === 'none' ? 25 : 587">
          <option value="starttls">STARTTLS</option><option value="ssl">SSL/TLS</option><option value="none">None</option></select></div></div>
      <div class="form-row"><label class="lbl">Username (optional)</label><input v-model="ch.username" class="input" autocomplete="off" placeholder="often the From address"></div>
      <div class="form-row"><label class="lbl">Password</label><PasswordInput v-model="ch.secret" autocomplete="new-password"
        :placeholder="hasSecret() ? 'Saved. Leave empty to keep it' : 'SMTP password or app password'" /></div>
      <div class="form-row"><label class="lbl">From</label><input v-model="ch.sender" class="input" placeholder="k10y@example.com"></div>
      <div class="form-row"><label class="lbl">To</label><input v-model="ch.recipients" class="input" placeholder="ops@example.com, oncall@example.com"></div>
    </template>
    <label class="pill" style="align-self:flex-start"><input v-model="ch.enabled" type="checkbox"> Enabled</label>
    <div v-if="ch.msg" class="test-result show" :class="ch.ok ? 'ok' : 'err'"><Icon :name="ch.ok ? 'ok' : 'crit'" /><span>{{ ch.msg }}</span></div>
    <template #actions><button class="btn ghost" type="button" @click="ch.open = false">Cancel</button><span style="flex:1" />
      <button class="btn primary" :disabled="ch.busy || !canSave()" @click="saveChannel">Save &amp; send test</button></template>
  </Modal>
</template>
