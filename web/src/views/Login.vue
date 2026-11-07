<script setup lang="ts">
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import Icon from "@/components/Icon.vue";
import PasswordInput from "@/components/PasswordInput.vue";
import { hub } from "@/api/client";
import { useSession } from "@/stores/session";
import AuthLayout from "./AuthLayout.vue";
const s = useSession(), router = useRouter(), route = useRoute();
let saved = "";
try { saved = localStorage.getItem("k10y.lastUser") || ""; } catch { /* storage blocked */ }
const username = ref(saved), password = ref(""), error = ref(""), busy = ref(false), caps = ref(false);
// Second step for accounts with two-factor login: the authenticator code (or a recovery code).
const step = ref<"password" | "code">("password"), code = ref("");
async function submit() {
  error.value = ""; busy.value = true;
  try {
    if (await s.login(username.value.trim(), password.value, step.value === "code" ? code.value.trim() : undefined) === "code") {
      step.value = "code"; return;
    }
    try { localStorage.setItem("k10y.lastUser", username.value.trim()); } catch { /* storage blocked */ }
    router.replace(s.me?.user.must_change_password ? "/password" : s.me?.user.must_setup_2fa ? "/two-factor" : String(route.query.next || "/"));
  } catch (e: any) {
    error.value = e.message;
    if (step.value === "code") code.value = ""; else password.value = "";
  } finally { busy.value = false; }
}
// Single sign-on buttons (public list; empty when none is set up).
const ssoProviders = ref<{ id: string; name: string }[]>([]);
hub.get<{ id: string; name: string }[]>("/auth/sso").then((x) => (ssoProviders.value = x)).catch(() => {});
function back() { step.value = "password"; code.value = ""; password.value = ""; error.value = ""; }
</script>
<template>
  <AuthLayout>
    <form v-if="step === 'code'" class="auth-form" novalidate @submit.prevent="submit">
      <div class="auth-head"><h1>Two-factor login</h1><p>Enter the 6-digit code from your authenticator app for <b>{{ username }}</b>.</p></div>
      <div class="form-row"><label class="lbl" for="code">Code</label>
        <div class="icon-input"><Icon name="lock" /><input id="code" v-model="code" class="input totp-input" autocomplete="one-time-code" inputmode="numeric" autofocus required placeholder="123 456" maxlength="20"></div>
        <span class="hint">Lost your phone? Type one of your recovery codes instead.</span></div>
      <div v-if="error" class="auth-error" role="alert"><Icon name="crit" /><span>{{ error }}</span></div>
      <button class="btn primary auth-submit" type="submit" :disabled="busy || code.trim().length < 6">
        <span v-if="busy" class="spinner" aria-hidden="true" />{{ busy ? "Checking…" : "Sign in" }}</button>
      <button class="btn ghost" type="button" @click="back">Use another account</button>
    </form>
    <form v-else class="auth-form" novalidate @submit.prevent="submit">
      <div class="auth-head"><h1>Welcome back</h1><p>Sign in to your k10y hub to continue.</p></div>
      <div class="form-row"><label class="lbl" for="u">Username</label>
        <div class="icon-input"><Icon name="user" /><input id="u" v-model="username" class="input" autocomplete="username" :autofocus="!username" required placeholder="your.name"></div></div>
      <div class="form-row"><label class="lbl" for="p">Password</label>
        <PasswordInput id="p" v-model="password" autocomplete="current-password" required placeholder="••••••••" :autofocus="!!username" @capslock="(on) => (caps = on)" />
        <span v-if="caps" class="caps-hint"><Icon name="warn" /> Caps Lock is on</span></div>
      <div v-if="error" class="auth-error" role="alert"><Icon name="crit" /><span>{{ error }}</span></div>
      <button class="btn primary auth-submit" type="submit" :disabled="busy || !username || !password">
        <span v-if="busy" class="spinner" aria-hidden="true" />{{ busy ? "Signing in…" : "Sign in" }}</button>
      <template v-if="ssoProviders.length">
        <div class="auth-or"><span>or</span></div>
        <a v-for="p in ssoProviders" :key="p.id" class="btn auth-sso" :href="`/api/hub/auth/sso/${p.id}/start`"><Icon name="key" /><span>Sign in with {{ p.name }}</span></a>
      </template>
      <p class="auth-note">Forgot your password? Ask an administrator to reset it in <b>Administration → Users</b>.</p>
    </form>
  </AuthLayout>
</template>

<style scoped>
.auth-or { display: flex; align-items: center; gap: 10px; color: var(--muted); font-size: 12px; }
.auth-or::before, .auth-or::after { content: ""; flex: 1; height: 1px; background: var(--border); }
.auth-sso { justify-content: center; text-decoration: none; }
.totp-input { font-family: var(--mono); letter-spacing: .2em; font-size: 18px; }
</style>
