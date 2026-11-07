<script setup lang="ts">
// Two-factor login for the signed-in user: set it up (scan a QR code, confirm a code, keep the recovery codes),
// make new recovery codes, or turn it off. Used in My settings and on the forced setup page.
import QRCode from "qrcode";
import { onMounted, reactive, ref } from "vue";
import { hub } from "@/api/client";
import Badge from "./Badge.vue";
import Icon from "./Icon.vue";
import PasswordInput from "./PasswordInput.vue";
import { useSession } from "@/stores/session";
import { toast } from "@/stores/ui";

const props = defineProps<{ forced?: boolean }>();
const emit = defineEmits<{ (e: "done"): void }>();
const s = useSession();
const status = ref<{ enabled: boolean; recovery_codes_left: number; required: boolean } | null>(null);
const f = reactive({ step: "idle" as "idle" | "password" | "scan" | "codes" | "disable" | "regen", password: "", code: "", secret: "", qr: "",
  codes: [] as string[], busy: false, error: "" });
async function load() { try { status.value = await hub.get("/auth/totp"); } catch { status.value = null; } }
onMounted(load);

function start(step: typeof f.step) { Object.assign(f, { step, password: "", code: "", error: "" }); }
async function run(fn: () => Promise<void>) {
  f.busy = true; f.error = "";
  try { await fn(); } catch (e: any) { f.error = e.message; } finally { f.busy = false; }
}
const setup = () => run(async () => {
  const r = await hub.post<{ secret: string; uri: string }>("/auth/totp/setup", { password: f.password });
  f.secret = r.secret; f.qr = await QRCode.toDataURL(r.uri, { margin: 1, width: 200 }); f.password = ""; f.step = "scan";
});
const enable = () => run(async () => {
  const r = await hub.post<{ recovery_codes: string[]; me: any }>("/auth/totp/enable", { code: f.code.trim() });
  s.me = r.me; f.codes = r.recovery_codes; f.step = "codes"; f.secret = ""; f.qr = ""; await load();
});
const disable = () => run(async () => {
  s.me = await hub.post("/auth/totp/disable", { password: f.password }); f.step = "idle"; await load(); toast(true, "Two-factor login is off");
});
const regen = () => run(async () => {
  f.codes = (await hub.post<{ recovery_codes: string[] }>("/auth/totp/recovery-codes", { password: f.password })).recovery_codes;
  f.step = "codes"; await load();
});
const grouped = (secret: string) => secret.replace(/(.{4})/g, "$1 ").trim();
async function copyCodes() { try { await navigator.clipboard.writeText(f.codes.join("\n")); toast(true, "Copied"); } catch { toast(false, "Copy failed"); } }
function downloadCodes() {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([`k10y recovery codes for ${s.me?.user.username}\nEach works once.\n\n${f.codes.join("\n")}\n`], { type: "text/plain" }));
  a.download = `k10y-recovery-codes-${s.me?.user.username}.txt`; a.click(); URL.revokeObjectURL(a.href);
}
function finish() { f.codes = []; f.step = "idle"; emit("done"); }
</script>

<template>
  <div class="tf">
    <!-- status -->
    <div v-if="f.step === 'idle' && status" class="pref-row"><div class="lbl">Two-factor login
        <div v-if="status.enabled">On. Signing in needs a code from your authenticator app. {{ status.recovery_codes_left }} recovery code{{ status.recovery_codes_left === 1 ? "" : "s" }} left.</div>
        <div v-else>Off. Add a code from an authenticator app (Google Authenticator, Microsoft Authenticator, 1Password, Authy…) to every sign-in.</div></div>
      <div class="tf-actions">
        <Badge :level="status.enabled ? 'good' : status.required ? 'warning' : 'neutral'" :label="status.enabled ? 'On' : status.required ? 'Required' : 'Off'" />
        <template v-if="status.enabled">
          <button class="btn sm" type="button" @click="start('regen')">New recovery codes</button>
          <button v-if="!status.required" class="btn ghost sm" type="button" @click="start('disable')">Turn off</button></template>
        <button v-else class="btn primary sm" type="button" @click="start('password')">Set up</button></div></div>

    <!-- confirm password (setup, disable, new recovery codes) -->
    <form v-if="['password', 'disable', 'regen'].includes(f.step)" class="tf-box" @submit.prevent="f.step === 'password' ? setup() : f.step === 'disable' ? disable() : regen()">
      <p class="hint">{{ f.step === "password" ? "Confirm it's you to start." : f.step === "disable" ? "Confirm your password to turn two-factor login off." : "Confirm your password. Your old recovery codes stop working." }}</p>
      <div class="form-row"><label class="lbl" for="tf-pw">Your password</label><PasswordInput id="tf-pw" v-model="f.password" autocomplete="current-password" required autofocus /></div>
      <div v-if="f.error" class="test-result show err"><Icon name="crit" /><span>{{ f.error }}</span></div>
      <div class="tf-row"><button v-if="!props.forced || f.step !== 'password'" class="btn ghost" type="button" @click="start('idle')">Cancel</button><span class="grow" />
        <button class="btn" :class="f.step === 'disable' ? 'danger' : 'primary'" type="submit" :disabled="f.busy || !f.password">
          {{ f.step === "password" ? "Continue" : f.step === "disable" ? "Turn off" : "Make new codes" }}</button></div>
    </form>

    <!-- scan and confirm -->
    <form v-if="f.step === 'scan'" class="tf-box" @submit.prevent="enable">
      <ol class="tf-steps">
        <li>Open your authenticator app and add an account by scanning this code.</li>
        <li>Type the 6-digit code it shows.</li></ol>
      <div class="tf-scan"><img :src="f.qr" alt="QR code to add k10y to your authenticator app" width="200" height="200">
        <div><p class="hint" style="margin:0 0 4px">Can't scan? Enter this key by hand (time-based):</p><code class="tf-secret">{{ grouped(f.secret) }}</code></div></div>
      <div class="form-row"><label class="lbl" for="tf-code">Code from the app</label>
        <input id="tf-code" v-model="f.code" class="input tf-code" autocomplete="one-time-code" inputmode="numeric" placeholder="123 456" maxlength="8" required autofocus></div>
      <div v-if="f.error" class="test-result show err"><Icon name="crit" /><span>{{ f.error }}</span></div>
      <div class="tf-row"><button v-if="!props.forced" class="btn ghost" type="button" @click="start('idle')">Cancel</button><span class="grow" />
        <button class="btn primary" type="submit" :disabled="f.busy || f.code.replace(/\D/g, '').length !== 6">Turn on</button></div>
    </form>

    <!-- recovery codes, shown once -->
    <div v-if="f.step === 'codes'" class="tf-box">
      <div class="test-result show ok"><Icon name="ok" /><span>Two-factor login is on. Keep these recovery codes somewhere safe: each one signs you in once if you lose your phone. They won't be shown again.</span></div>
      <ul class="tf-codes"><li v-for="c in f.codes" :key="c" class="mono">{{ c }}</li></ul>
      <div class="tf-row"><button class="btn" type="button" @click="copyCodes"><Icon name="copy" /><span>Copy</span></button>
        <button class="btn" type="button" @click="downloadCodes"><Icon name="upload" style="transform:rotate(180deg)" /><span>Download</span></button><span class="grow" />
        <button class="btn primary" type="button" @click="finish">I've saved them</button></div>
    </div>
  </div>
</template>

<style scoped>
.tf { display: flex; flex-direction: column; gap: 12px; }
.tf-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; justify-content: flex-end; }
.tf-box { display: flex; flex-direction: column; gap: 12px; padding: 14px; border: 1px solid var(--border); border-radius: 10px; }
.tf-box .hint { margin: 0; }
.tf-row { display: flex; gap: 8px; align-items: center; }
.tf-steps { margin: 0; padding-left: 18px; line-height: 1.6; font-size: 13.5px; }
.tf-scan { display: flex; gap: 16px; align-items: center; flex-wrap: wrap; }
.tf-scan img { border-radius: 8px; background: #fff; padding: 6px; }
.tf-secret { font-size: 14px; letter-spacing: .06em; word-break: break-all; }
.tf-code { font-family: var(--mono); letter-spacing: .2em; font-size: 18px; max-width: 200px; }
.tf-codes { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px 16px; margin: 0; padding: 12px 16px; list-style: none;
  border-radius: 8px; background: var(--surface-2, var(--surface-active)); font-size: 15px; }
</style>
