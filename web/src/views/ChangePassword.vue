<script setup lang="ts">
import { computed, ref } from "vue";
import { useRouter } from "vue-router";
import Icon from "@/components/Icon.vue";
import { useSession } from "@/stores/session";
const s = useSession(), router = useRouter();
const current = ref(""), next = ref(""), again = ref(""), error = ref(""), busy = ref(false);
const forced = computed(() => s.me?.user.must_change_password);
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
  <div class="auth-wrap">
    <form class="auth-card" @submit.prevent="submit">
      <div class="brand"><div class="brand-mark"><Icon name="logo" /></div><span>k10y</span></div>
      <h1>{{ forced ? "Set a new password" : "Change password" }}</h1>
      <p v-if="forced" class="hint" style="margin:0">Your account needs a new password before you continue. Use at least 8 characters with letters and numbers or symbols.</p>
      <div class="form-row"><label class="lbl" for="c">Current password</label><input id="c" v-model="current" class="input" type="password" autocomplete="current-password" required></div>
      <div class="form-row"><label class="lbl" for="n">New password</label><input id="n" v-model="next" class="input" type="password" autocomplete="new-password" required></div>
      <div class="form-row"><label class="lbl" for="a">Repeat new password</label><input id="a" v-model="again" class="input" type="password" autocomplete="new-password" required></div>
      <div v-if="error" class="err-text" role="alert">{{ error }}</div>
      <button class="btn primary" type="submit" :disabled="busy || !current || !next">{{ busy ? "Saving…" : "Save password" }}</button>
      <button v-if="forced" class="btn ghost" type="button" @click="logout">Sign out</button>
      <button v-else class="btn ghost" type="button" @click="router.back()">Cancel</button>
    </form>
  </div>
</template>
