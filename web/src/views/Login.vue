<script setup lang="ts">
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import Icon from "@/components/Icon.vue";
import { useSession } from "@/stores/session";
const s = useSession(), router = useRouter(), route = useRoute();
const username = ref(""), password = ref(""), error = ref(""), busy = ref(false);
async function submit() {
  error.value = ""; busy.value = true;
  try {
    await s.login(username.value.trim(), password.value);
    router.replace(s.me?.user.must_change_password ? "/password" : String(route.query.next || "/"));
  } catch (e: any) { error.value = e.message; } finally { busy.value = false; }
}
</script>
<template>
  <div class="auth-wrap">
    <form class="auth-card" @submit.prevent="submit">
      <div class="brand"><div class="brand-mark"><Icon name="logo" /></div><span>k10y</span></div>
      <h1>Sign in</h1>
      <div class="form-row"><label class="lbl" for="u">Username</label><input id="u" v-model="username" class="input" autocomplete="username" autofocus required></div>
      <div class="form-row"><label class="lbl" for="p">Password</label><input id="p" v-model="password" class="input" type="password" autocomplete="current-password" required></div>
      <div v-if="error" class="err-text" role="alert">{{ error }}</div>
      <button class="btn primary" type="submit" :disabled="busy || !username || !password">{{ busy ? "Signing in…" : "Sign in" }}</button>
    </form>
  </div>
</template>
