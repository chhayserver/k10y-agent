<script setup lang="ts">
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import Icon from "@/components/Icon.vue";
import PasswordInput from "@/components/PasswordInput.vue";
import { useSession } from "@/stores/session";
import AuthLayout from "./AuthLayout.vue";
const s = useSession(), router = useRouter();
const current = ref(""), next = ref(""), again = ref(""), error = ref(""), busy = ref(false);
const forced = computed(() => s.me?.user.must_change_password);
// Mirrors the server policy: at least 8 characters, not only letters or only digits.
const checks = computed(() => [
  { ok: next.value.length >= 8, text: "At least 8 characters" },
  { ok: /[a-zA-Z]/.test(next.value) && /[^a-zA-Z]/.test(next.value), text: "Letters plus numbers or symbols" },
  { ok: !!next.value && next.value === again.value, text: "Both new passwords match" },
]);
const strength = computed(() => {
  const v = next.value; if (!v) return 0;
  let n = 0; if (v.length >= 8) n++; if (v.length >= 12) n++; if (/[A-Z]/.test(v) && /[a-z]/.test(v)) n++; if (/\d/.test(v)) n++; if (/[^\w]/.test(v)) n++;
  return Math.min(4, Math.max(1, n - 1));
});
const LABEL = ["", "Weak", "Fair", "Good", "Strong"];
async function submit() {
  error.value = "";
  if (next.value !== again.value) { error.value = "The new passwords don't match."; return; }
  busy.value = true;
  try { await s.changePassword(current.value, next.value); router.replace("/"); }
  catch (e: any) { error.value = e.message; } finally { busy.value = false; }
}
async function logout() { await s.logout(); router.replace("/login"); }
</script>
<template>
  <AuthLayout>
    <form class="auth-form" novalidate @submit.prevent="submit">
      <div class="auth-head"><h1>{{ forced ? "Set a new password" : "Change password" }}</h1>
        <p>{{ forced ? "For your security, choose a new password before you continue." : `Signed in as ${s.me?.user.username}.` }}</p></div>
      <div class="form-row"><label class="lbl" for="c">Current password</label><PasswordInput id="c" v-model="current" autocomplete="current-password" required /></div>
      <div class="form-row"><label class="lbl" for="n">New password</label><PasswordInput id="n" v-model="next" autocomplete="new-password" required />
        <div v-if="next" class="pw-strength" :data-level="strength"><span v-for="i in 4" :key="i" :class="{ on: i <= strength }" /><em>{{ LABEL[strength] }}</em></div></div>
      <div class="form-row"><label class="lbl" for="a">Repeat new password</label><PasswordInput id="a" v-model="again" autocomplete="new-password" required /></div>
      <ul class="pw-checks"><li v-for="c in checks" :key="c.text" :class="{ ok: c.ok }"><Icon :name="c.ok ? 'ok' : 'dot'" />{{ c.text }}</li></ul>
      <div v-if="error" class="auth-error" role="alert"><Icon name="crit" /><span>{{ error }}</span></div>
      <button class="btn primary auth-submit" type="submit" :disabled="busy || !current || !checks.every((c) => c.ok)">
        <span v-if="busy" class="spinner" aria-hidden="true" />{{ busy ? "Saving…" : "Save password" }}</button>
      <button v-if="forced" class="btn ghost" type="button" @click="logout">Sign out</button>
      <button v-else class="btn ghost" type="button" @click="router.back()">Cancel</button>
    </form>
  </AuthLayout>
</template>
