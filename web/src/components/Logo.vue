<script setup lang="ts">
// k10y-agent mark: an isometric cube (a cluster node) with a live "pulse" dot, plus the wordmark.
defineProps<{ size?: number; wordmark?: boolean; light?: boolean }>();
const uid = `lg${Math.random().toString(36).slice(2, 8)}`;
</script>
<template>
  <span class="k10y-logo" :class="{ light }">
    <svg :width="size || 40" :height="size || 40" viewBox="0 0 48 48" role="img" aria-label="k10y-agent">
      <defs>
        <linearGradient :id="`${uid}-bg`" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#3b8cf0" /><stop offset="1" stop-color="#5b4ee0" /></linearGradient>
        <linearGradient :id="`${uid}-top`" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" /><stop offset="1" stop-color="#dfe9ff" /></linearGradient>
      </defs>
      <rect width="48" height="48" rx="13" :fill="`url(#${uid}-bg)`" />
      <rect x="0.5" y="0.5" width="47" height="47" rx="12.5" fill="none" stroke="#fff" stroke-opacity=".18" />
      <!-- cube -->
      <path d="M24 9.5 36.5 16.7 24 23.9 11.5 16.7Z" :fill="`url(#${uid}-top)`" />
      <path d="M11.5 16.7 24 23.9V38.5L11.5 31.3Z" fill="#fff" fill-opacity=".72" />
      <path d="M36.5 16.7 24 23.9V38.5L36.5 31.3Z" fill="#fff" fill-opacity=".45" />
      <!-- "k" carved in the left face -->
      <path d="M15.6 21.6v9.6M15.6 27.4l4.6-4.2M17.4 25.8l3.2 5.6" stroke="#4a63e6" stroke-width="1.9" stroke-linecap="round" fill="none" />
      <!-- pulse -->
      <circle cx="36.5" cy="11.5" r="4.6" fill="#22c58b" stroke="#fff" stroke-width="2" />
    </svg>
    <span v-if="wordmark" class="k10y-word"><b>k10y</b><span>-agent</span></span>
  </span>
</template>
