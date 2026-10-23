<script setup lang="ts">
// A product screenshot in a browser-like frame. Used on the homepage and in the docs.
import { withBase } from "vitepress";
defineProps<{ src: string; alt: string; caption?: string; url?: string }>();
</script>
<template>
  <figure class="k-shot">
    <div class="k-shot-bar"><span /><span /><span /><em>{{ url || "https://k10y.your-company.internal" }}</em></div>
    <img class="k-shot-img" :src="withBase(src)" :alt="alt" loading="lazy" decoding="async">
    <figcaption v-if="caption">{{ caption }}</figcaption>
  </figure>
</template>
<style scoped>
.k-shot { margin: 20px 0; border-radius: 14px; overflow: hidden; border: 1px solid var(--vp-c-divider); background: #111318;
  box-shadow: 0 30px 60px -30px rgba(20, 30, 80, .45); }
.k-shot-bar { display: flex; align-items: center; gap: 6px; padding: 10px 14px; background: #1b1d24; border-bottom: 1px solid rgba(255,255,255,.06); }
.k-shot-bar span { width: 10px; height: 10px; border-radius: 50%; background: #3a3d48; }
.k-shot-bar span:nth-child(1) { background: #ff5f57; } .k-shot-bar span:nth-child(2) { background: #febc2e; } .k-shot-bar span:nth-child(3) { background: #28c840; }
.k-shot-bar em { margin-left: 12px; font-style: normal; font-size: 12px; color: #8b90a0; font-family: var(--vp-font-family-mono);
  background: #12141a; padding: 3px 10px; border-radius: 6px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.k-shot img { display: block; width: 100%; height: auto; margin: 0 !important; border-radius: 0 !important; }
figcaption { padding: 10px 14px; font-size: 13px; color: #9aa0b0; background: #15171d; border-top: 1px solid rgba(255,255,255,.06); }
</style>
