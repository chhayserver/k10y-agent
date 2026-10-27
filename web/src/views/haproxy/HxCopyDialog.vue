<script setup lang="ts">
// Copy a frontend (optionally with its backends) or a backend with its servers to another HAProxy.
// Things that already exist there are skipped; the change is then reviewed and validated on that HAProxy.
import { computed, ref, watch } from "vue";
import Icon from "@/components/Icon.vue";
import Modal from "@/components/Modal.vue";
import { hx, type HxBackend, type HxFrontend, type HxOp, type HxOverview } from "@/lib/haproxy";
import { backendCopyOps, frontendCopyOp } from "@/lib/haproxyCatalog";

const props = defineProps<{ open: boolean; kind: "frontend" | "backend"; item: HxFrontend | HxBackend | null; source: HxOverview | null; instances: any[]; currentId: number }>();
const emit = defineEmits<{ close: []; review: [ops: HxOp[], title: string, target: { id: number; name: string }] }>();
const targetId = ref(0), withBackends = ref(true), there = ref<HxOverview | null>(null), loading = ref(false), err = ref("");
const others = computed(() => (props.instances || []).filter((i) => i.id !== props.currentId));
watch(() => props.open, (o) => { if (o) { err.value = ""; withBackends.value = true; targetId.value = others.value[0]?.id || 0; } });
watch(targetId, async (id) => {
  there.value = null; err.value = "";
  if (!id) return;
  loading.value = true;
  try { there.value = await hx.overview(id); } catch (e: any) { err.value = `Can't read that HAProxy: ${e.message}`; } finally { loading.value = false; }
});

const plan = computed(() => {
  const t = there.value, it = props.item;
  if (!t || !it) return null;
  const haveB = new Set(t.backends.map((b) => b.name)), haveF = new Set(t.frontends.map((f) => f.name));
  const ops: HxOp[] = [], skipped: string[] = [];
  let names: string[] = [];
  if (props.kind === "backend") names = [it.name];
  else {
    const fe = it as HxFrontend;
    names = withBackends.value ? [...new Set([fe.default_backend, ...fe.routes.map((r) => r.backend)].filter(Boolean) as string[])] : [];
  }
  for (const n of names) {
    const b = props.source?.backends.find((x) => x.name === n);
    if (!b) continue;
    if (haveB.has(n)) skipped.push(`backend ${n} (already there)`); else ops.push(...backendCopyOps(b));
  }
  if (props.kind === "frontend") {
    if (haveF.has(it.name)) return { ops: [], skipped, blocked: `A frontend named ${it.name} already exists on that HAProxy.` };
    ops.push(frontendCopyOp(it as HxFrontend));
  }
  return { ops, skipped, blocked: ops.length ? "" : "Everything already exists on that HAProxy." };
});
function go() {
  const t = others.value.find((i) => i.id === targetId.value);
  if (!plan.value || plan.value.blocked || !t) return;
  emit("review", plan.value.ops, `Copy ${props.kind} ${props.item!.name} to ${t.name}`, { id: t.id, name: t.name });
}
</script>

<template>
  <Modal :open="open && !!item" :title="`Copy ${kind} ${item?.name} to another HAProxy`" @close="emit('close')">
    <p v-if="!others.length" class="hint" style="margin:0">There's no other HAProxy yet. Add one under <b>Administration → HAProxy instances</b>.</p>
    <template v-else>
      <div class="form-row"><label class="lbl" for="hxc-target">Copy to</label>
        <select id="hxc-target" v-model="targetId" class="select" style="max-width:280px"><option v-for="i in others" :key="i.id" :value="i.id">{{ i.name }}</option></select></div>
      <label v-if="kind === 'frontend'" class="pill" style="align-self:flex-start"><input v-model="withBackends" type="checkbox"> Also copy the backends it uses (with their servers)</label>
      <p v-if="loading" class="hint" style="margin:0">Reading that HAProxy…</p>
      <template v-else-if="plan">
        <ul class="hxc-list">
          <li v-for="(o, i) in plan.ops.filter((x) => x.kind !== 'server')" :key="i"><Icon name="ok" /> create {{ o.kind }} <b class="mono">{{ o.name }}</b>
            <span v-if="o.kind === 'backend'" class="hint"> with {{ plan.ops.filter((x) => x.kind === 'server' && x.backend === o.name).length }} servers</span></li>
          <li v-for="s in plan.skipped" :key="s" class="dim"><Icon name="pending" /> skip {{ s }}</li></ul>
        <div v-if="plan.blocked" class="test-result show err"><Icon name="crit" /><span>{{ plan.blocked }}</span></div>
        <p v-else-if="kind === 'frontend' && (item as HxFrontend).binds.some((b) => b.ssl)" class="hint" style="margin:0">It uses TLS: the same certificate must be uploaded on that HAProxy too, or HAProxy will refuse the change in the review.</p>
      </template>
    </template>
    <div v-if="err" class="test-result show err"><Icon name="crit" /><span>{{ err }}</span></div>
    <template #actions><button class="btn ghost" type="button" @click="emit('close')">Cancel</button><span style="flex:1" />
      <button class="btn primary" type="button" :disabled="!plan || !!plan.blocked" @click="go">Review on {{ others.find((i) => i.id === targetId)?.name || "…" }}</button></template>
  </Modal>
</template>

<style scoped>
.mono { font-family: var(--mono); }
.hxc-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; font-size: 13.5px; }
.hxc-list .icon { width: 14px; height: 14px; vertical-align: -2px; color: var(--good); }
.hxc-list .dim .icon { color: var(--muted); }
</style>
