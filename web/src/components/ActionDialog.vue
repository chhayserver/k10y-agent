<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { agent } from "@/api/client";
import { useCluster } from "@/stores/cluster";
import { useSession } from "@/stores/session";
import { action, toast } from "@/stores/ui";
import Icon from "./Icon.vue";

const dlg = ref<HTMLDialogElement | null>(null);
const typed = ref("");
const busy = ref(false);
const result = ref<{ ok: boolean; message: string; lines: string[] } | null>(null);
const cfg = computed(() => action.cfg);
const confirmed = computed(() => !cfg.value?.confirmText || typed.value.trim() === cfg.value.confirmText);

watch(cfg, async (c) => {
  typed.value = ""; result.value = null; busy.value = false;
  await nextTick();
  if (c && dlg.value && !dlg.value.open) dlg.value.showModal();
  if (!c && dlg.value?.open) dlg.value.close();
  (dlg.value?.querySelector("input, select, textarea") as HTMLElement | null)?.focus();
});

function lines(res: any): string[] {
  if (res?.results) return res.results.map((r: any) => `${r.object}${r.ok ? "" : ` – ${r.error}`}`);
  if (res?.evicted) return [...res.evicted.map((p: string) => `evict ${p}`), ...res.skipped.map((p: any) => `skip ${p.pod} (${p.reason})`),
    ...res.failed.map((p: any) => `failed ${p.pod}: ${p.reason}`)];
  return [];
}

async function submit(dryRun: boolean) {
  const c = cfg.value;
  if (!c) return;
  busy.value = true; result.value = null;
  try {
    if (c.run) {
      const msg = await c.run(c.model || {});
      close();
      if (msg) toast(true, msg);
      return;
    }
    const req = c.request!(c.model || {}, dryRun);
    const a = agent(useSession().current!.id);
    const res = req.method === "DELETE" ? await a.del(req.path, { ...(req.query || {}), dry_run: dryRun }) : await a.post(req.path, req.body);
    if (dryRun) { result.value = { ok: true, message: res.message, lines: lines(res) }; return; }
    close();
    toast(true, res.message.charAt(0).toUpperCase() + res.message.slice(1));
    c.after?.(res);
    useCluster().refresh();
  } catch (e: any) {
    result.value = { ok: false, message: e.message, lines: [] };
  } finally {
    busy.value = false;
  }
}
function close() { action.cfg = null; }
</script>

<template>
  <dialog ref="dlg" :class="{ wide: cfg?.wide }" @close="close">
    <form v-if="cfg" novalidate @submit.prevent="submit(false)">
      <h2>{{ cfg.title }}</h2>
      <div v-if="cfg.desc" class="desc" v-html="cfg.desc" />
      <component :is="cfg.form" v-if="cfg.form" :model="cfg.model" />
      <div v-if="cfg.confirmText" class="form-row">
        <label class="lbl" for="act-confirm">Type <code>{{ cfg.confirmText }}</code> to confirm</label>
        <input id="act-confirm" v-model="typed" class="input" autocomplete="off" spellcheck="false">
      </div>
      <div v-if="result" class="act-result show" :class="result.ok ? 'ok' : 'err'" role="status">
        <div class="head"><Icon :name="result.ok ? 'ok' : 'crit'" /><span>{{ result.message }}</span></div>
        <ul v-if="result.lines.length"><li v-for="l in result.lines" :key="l">{{ l }}</li></ul>
      </div>
      <div class="actions">
        <button class="btn ghost" type="button" @click="close">Cancel</button>
        <span style="flex:1" />
        <button v-if="!cfg.noDryRun && !cfg.run" class="btn" type="button" :disabled="busy" title="Validate on the server without changing anything" @click="submit(true)">Dry run</button>
        <button class="btn" :class="cfg.danger ? 'danger' : 'primary'" type="submit" :disabled="busy || !confirmed">{{ cfg.confirmLabel || "Confirm" }}</button>
      </div>
    </form>
  </dialog>
</template>
