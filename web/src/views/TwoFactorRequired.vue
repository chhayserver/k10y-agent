<script setup lang="ts">
// Shown after sign-in when the hub requires two-factor login for this account and it isn't set up yet.
import { onMounted } from "vue";
import { useRouter } from "vue-router";
import TwoFactorSetup from "@/components/TwoFactorSetup.vue";
import { loadAccountPrefs } from "@/stores/prefs";
import { useSession } from "@/stores/session";
import AuthLayout from "./AuthLayout.vue";
const s = useSession(), router = useRouter();
onMounted(() => { if (!s.me?.user.must_setup_2fa) router.replace("/"); });
async function done() { await loadAccountPrefs(); router.replace("/"); }
async function logout() { await s.logout(); router.replace("/login"); }
</script>
<template>
  <AuthLayout>
    <div class="auth-form">
      <div class="auth-head"><h1>Set up two-factor login</h1>
        <p>Your administrator requires a code from an authenticator app at every sign-in. Set it up once to continue.</p></div>
      <TwoFactorSetup forced @done="done" />
      <button class="btn ghost" type="button" @click="logout">Sign out</button>
    </div>
  </AuthLayout>
</template>
