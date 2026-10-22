<script setup lang="ts">
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import Icon from "@/components/Icon.vue";
import PasswordInput from "@/components/PasswordInput.vue";
import { useSession } from "@/stores/session";
import AuthLayout from "./AuthLayout.vue";
const s = useSession(), router = useRouter(), route = useRoute();
let saved = "";
try { saved = localStorage.getItem("k10y.lastUser") || ""; } catch { /* storage blocked */ }
const username = ref(saved), password = ref(""), error = ref(""), busy = ref(false), caps = ref(false);
async function submit() {
  error.value = ""; busy.value = true;
  try {
    await s.login(username.value.trim(), password.value);
    try { localStorage.setItem("k10y.lastUser", username.value.trim()); } catch { /* storage blocked */ }
    router.replace(s.me?.user.must_change_password ? "/password" : String(route.query.next || "/"));
  } catch (e: any) { error.value = e.message; password.value = ""; } finally { busy.value = false; }
}
</script>
<template>
  <AuthLayout>
    <form class="auth-form" novalidate @submit.prevent="submit">
      <div class="auth-head"><h1>Welcome back</h1><p>Sign in to your k10y hub to continue.</p></div>
      <div class="form-row"><label class="lbl" for="u">Username</label>
        <div class="icon-input"><Icon name="user" /><input id="u" v-model="username" class="input" autocomplete="username" :autofocus="!username" required placeholder="your.name"></div></div>
      <div class="form-row"><label class="lbl" for="p">Password</label>
        <PasswordInput id="p" v-model="password" autocomplete="current-password" required placeholder="••••••••" :autofocus="!!username" @capslock="(on) => (caps = on)" />
        <span v-if="caps" class="caps-hint"><Icon name="warn" /> Caps Lock is on</span></div>
      <div v-if="error" class="auth-error" role="alert"><Icon name="crit" /><span>{{ error }}</span></div>
      <button class="btn primary auth-submit" type="submit" :disabled="busy || !username || !password">
        <span v-if="busy" class="spinner" aria-hidden="true" />{{ busy ? "Signing in…" : "Sign in" }}</button>
      <p class="auth-note">Forgot your password? Ask an administrator to reset it in <b>Administration → Users</b>.</p>
    </form>
  </AuthLayout>
</template>
