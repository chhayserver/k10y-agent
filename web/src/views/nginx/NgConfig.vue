<script setup lang="ts">
// The configuration as Nginx reads it (`nginx -T`: every included file), plus any problem reading it.
import { onMounted, ref } from "vue";
import Icon from "@/components/Icon.vue";
import { ng } from "@/lib/nginx";
import { useNginx } from "@/stores/nginx";
import { toast } from "@/stores/ui";

const n = useNginx();
const text = ref(""), check = ref(""), error = ref(""), loading = ref(true);
onMounted(async () => {
  try { const r = await ng.raw(n.currentId!); text.value = r.text; check.value = r.check; } catch (e: any) { error.value = e.message; } finally { loading.value = false; }
});
async function copy() { try { await navigator.clipboard.writeText(text.value); toast(true, "Copied"); } catch { toast(false, "Copy failed"); } }
</script>

<template>
  <div class="toolbar"><span class="hint" style="flex:1">Every file Nginx loads, in order (<code>nginx -T</code>). Editing from k10y comes next; for now this is read-only.</span>
    <button class="btn" type="button" :disabled="!text" @click="copy"><Icon name="copy" /><span>Copy</span></button></div>
  <div v-if="error" class="banner show"><Icon name="crit" /><span class="msg">{{ error }}</span></div>
  <div v-if="n.data?.config.errors.length" class="banner show"><Icon name="warn" /><span class="msg">
    <span v-for="(e, i) in n.data.config.errors" :key="i" style="display:block">{{ e.file }}:{{ e.line }}: {{ e.message }}</span></span></div>
  <p v-if="check" class="hint mono">{{ check }}</p>
  <pre class="logs" style="max-height:none;white-space:pre">{{ loading ? "Loading…" : text }}</pre>
</template>
