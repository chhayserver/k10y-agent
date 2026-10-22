<script setup lang="ts">
// Shared shell for the sign-in and password pages: brand panel + form panel.
import Logo from "@/components/Logo.vue";
</script>
<template>
  <div class="auth">
    <aside class="auth-brand" aria-hidden="false">
      <div class="auth-grid" aria-hidden="true" />
      <div class="auth-glow" aria-hidden="true" />
      <Logo :size="44" wordmark light />
      <div class="auth-pitch">
        <h2>Every cluster.<br>One calm place.</h2>
        <p>Monitor, control and troubleshoot your Kubernetes clusters with live metrics, alerts and an AI assistant.</p>
        <ul>
          <li><span class="dot" />Live health, CPU and memory across clusters</li>
          <li><span class="dot" />Safe actions with dry run, roles and full audit</li>
          <li><span class="dot" />Alerts to Telegram and live log streaming</li>
        </ul>
      </div>
      <div class="auth-nodes" aria-hidden="true">
        <span v-for="i in 12" :key="i" :class="{ warn: i === 7, off: i === 11 }" />
      </div>
    </aside>
    <main class="auth-main">
      <div class="auth-mobile-logo"><Logo :size="40" wordmark /></div>
      <slot />
      <p class="auth-foot">k10y-agent · secured with HTTPS</p>
    </main>
  </div>
</template>
