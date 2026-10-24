<script setup lang="ts">
// AI assistant for HAProxy: read-only investigation, then proposed fixes that go through the normal review.
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { hub } from "@/api/client";
import Badge from "@/components/Badge.vue";
import EmptyState from "@/components/EmptyState.vue";
import Icon from "@/components/Icon.vue";
import type { Level } from "@/lib/format";
import type { HxOp, HxOverview } from "@/lib/haproxy";
import { md } from "@/lib/markdown";
import { to } from "@/lib/nav";
import { useSession } from "@/stores/session";

const props = defineProps<{ instanceId: number; data: HxOverview | null; canOperate: boolean; canConfigure: boolean }>();
const emit = defineEmits<{ review: [ops: HxOp[], title: string]; state: [backend: string, server: string, state: "ready" | "drain" | "maint"] }>();
const s = useSession(), route = useRoute();
const scope = ref<"instance" | "service" | "backend">("instance"), target = ref(""), question = ref("");
const running = ref(false), elapsed = ref(0), error = ref(""), result = ref<any>(null), applied = ref<Record<number, boolean>>({});
const provider = ref<any>(null);
const canAi = computed(() => s.canGlobal("ai.use"));
let timer = 0;
onMounted(async () => {
  try { provider.value = await hub.get("/settings/ai"); } catch { /* shown below */ }
  const q = route.query;
  if (q.scope === "service" || q.scope === "backend") { scope.value = q.scope; target.value = String(q.target || ""); }
  if (q.run && canAi.value) run();
});
onBeforeUnmount(() => clearInterval(timer));
const targets = computed(() => scope.value === "service" ? (props.data?.frontends || []).filter((f) => !f.stats_page).map((f) => f.name)
  : scope.value === "backend" ? (props.data?.backends || []).map((b) => b.name) : []);
watch(scope, () => { if (!targets.value.includes(target.value)) target.value = targets.value[0] || ""; });

async function run() {
  running.value = true; error.value = ""; result.value = null; applied.value = {}; elapsed.value = 0;
  clearInterval(timer); timer = window.setInterval(() => elapsed.value++, 1000);
  try { result.value = await hub.post(`/haproxy/${props.instanceId}/ai/diagnose`, { scope: scope.value, target: target.value, question: question.value }, 600000); }
  catch (e: any) { error.value = e.message; } finally { running.value = false; clearInterval(timer); }
}
const SEV: Record<string, Level> = { critical: "critical", warning: "warning", info: "neutral" };
const RISK: Record<string, [Level, string]> = { low: ["good", "Low risk"], medium: ["warning", "Medium risk"], high: ["critical", "High risk"] };
const STATE_WORD: Record<string, string> = { ready: "Put back in service", drain: "Drain", maint: "Maintenance" };
function title(a: any) {
  if (a.type === "set_server_state") return `${STATE_WORD[a.state] || a.state}: ${a.backend}/${a.server}`;
  if (a.type === "set_weight") return `Weight of ${a.backend}/${a.server} → ${a.weight}`;
  if (a.type === "update_server") return `Change server ${a.backend}/${a.server}${a.address ? ` → ${a.address}` : ""}${a.port ? `:${a.port}` : ""}`;
  return `Change backend ${a.backend}${a.balance ? ` · ${a.balance}` : ""}${a.check_type ? ` · health check ${a.check_type}${a.check_uri ? ` ${a.check_uri}` : ""}` : ""}`;
}
const allowed = (a: any) => !a.blocked && (a.state_change ? props.canOperate : props.canConfigure);
function apply(a: any, i: number) {
  if (a.state_change) emit("state", a.state_change.backend, a.state_change.server, a.state_change.state);
  else emit("review", a.ops, `AI suggestion: ${title(a)}`);
  applied.value[i] = true;
}
const SCOPES: [string, string][] = [["instance", "Whole HAProxy"], ["service", "One service"], ["backend", "One backend"]];
</script>
<template>
  <div class="hx-ai">
    <section class="card"><div class="card-head"><h2>Ask the AI</h2></div><div class="card-body" style="display:flex;flex-direction:column;gap:12px">
      <EmptyState v-if="!canAi" title="You need the ai.use permission" icon="lock" sub="Ask an administrator to add it to your role (on all clusters)." />
      <template v-else>
        <div class="segmented" role="group" aria-label="Scope"><button v-for="[v, l] in SCOPES" :key="v" :aria-pressed="scope === v" @click="scope = v as any">{{ l }}</button></div>
        <select v-if="scope !== 'instance'" v-model="target" class="select" :aria-label="scope">
          <option v-for="n in targets" :key="n" :value="n">{{ n }}</option></select>
        <textarea v-model="question" class="input" rows="3" style="height:auto;padding:8px" placeholder="Optional: what do you want to know? e.g. why are users getting 503 on the shop?" />
        <button class="btn primary" style="align-self:flex-start" :disabled="running || (scope !== 'instance' && !target)" @click="run"><Icon name="ai" /><span>{{ running ? "Investigating…" : "Investigate" }}</span></button>
        <p class="hint" style="margin:0">The AI reads server states, health checks, live rates, recent logs, config sections (passwords removed), certificates and recent changes. It can't change anything: you review every suggestion.</p>
      </template>
    </div></section>
    <section class="card"><div class="card-head"><h2>AI provider</h2><div v-if="s.canGlobal('settings.manage')" class="right"><RouterLink class="btn sm" :to="to('system')">Settings</RouterLink></div></div>
      <div class="card-body"><template v-if="provider"><b>{{ provider.provider_label || provider.provider }}</b><div class="hint">{{ provider.providers?.[provider.provider]?.model || "no model selected" }}</div>
        <div v-if="!provider.providers?.[provider.provider]?.configured" class="hint" style="color:var(--critical);margin-top:6px">No API key yet: add one in System settings → AI providers.</div></template>
        <span v-else class="hint">Loading…</span>
        <p class="hint" style="margin:10px 0 0">The data the AI reads is sent to this provider.</p></div></section>
  </div>

  <section v-if="running" class="card" style="margin-top:16px"><div class="ai-running"><span class="spinner" /><div><b>Investigating…</b><div class="hint">Reading server states, logs and config. {{ elapsed }}s</div></div></div></section>
  <div v-else-if="error" class="banner show" style="margin-top:16px"><Icon name="crit" /><span class="msg">{{ error }}</span><button class="btn" @click="run">Retry</button></div>
  <template v-else-if="result">
    <section class="card" style="margin-top:16px"><div class="card-head"><h2>Summary</h2><div v-if="result.incomplete" class="right"><Badge level="warning" label="Incomplete" /></div></div>
      <div class="card-body ai-md" v-html="md(result.summary)" /></section>
    <section class="card flush" style="margin-top:16px"><div class="card-head"><h2>Issues</h2><span class="sub">{{ result.issues.length }}</span></div>
      <div class="card-body">
        <EmptyState v-if="!result.issues.length" title="No issues found" icon="ok" good />
        <div v-for="(i, k) in result.issues" :key="k" class="ai-issue"><div class="ttl"><Badge :level="SEV[i.severity] || 'neutral'" :label="i.severity || 'info'" />{{ i.title }}<span v-if="i.resource" class="ns">{{ i.resource }}</span></div>
          <div>{{ i.root_cause }}</div><div v-if="i.evidence" class="ev">{{ i.evidence }}</div></div>
      </div></section>
    <section class="card flush" style="margin-top:16px"><div class="card-head"><h2>Proposed fixes</h2><span class="sub">{{ result.actions.length ? `${result.actions.length} · nothing has been changed yet` : "none" }}</span></div>
      <div class="card-body">
        <div v-if="!result.actions.length" class="hint" style="padding:0 18px 16px">The AI did not propose any change.</div>
        <div v-for="(a, i) in result.actions" :key="i" class="ai-action" :class="{ applied: applied[Number(i)] }">
          <div class="ttl"><Icon :name="a.type === 'set_server_state' ? 'pause' : a.type === 'set_weight' ? 'scale' : 'file'" />{{ title(a) }}
            <Badge :level="(RISK[a.risk] || RISK.medium)[0]" :label="(RISK[a.risk] || RISK.medium)[1]" /><Badge v-if="applied[Number(i)]" level="good" label="Opened for review" /></div>
          <div style="color:var(--text-2)">{{ a.reason }}</div>
          <div v-if="a.blocked" class="callout"><Icon name="lock" /><span>k10y won't offer this: {{ a.blocked }}.</span></div>
          <div class="row">
            <button v-if="allowed(a)" class="btn primary sm" @click="apply(a, Number(i))">Review &amp; apply…</button>
            <span v-else-if="!a.blocked" class="hint">You don't have permission to apply this.</span>
          </div>
        </div>
      </div></section>
    <details class="ai-steps"><summary>Investigation: {{ result.steps.length }} tool call(s) · {{ result.provider }}/{{ result.model }} · {{ result.seconds }}s</summary>
      <ol><li v-for="(st, k) in result.steps" :key="k">{{ st.tool }}({{ Object.entries(st.args || {}).map(([x, y]) => `${x}=${y}`).join(", ") }})</li></ol></details>
  </template>
</template>
<style scoped>
.hx-ai { display: grid; grid-template-columns: minmax(0, 2fr) minmax(240px, 1fr); gap: 16px; align-items: start; }
@media (max-width: 900px) { .hx-ai { grid-template-columns: minmax(0, 1fr); } }
</style>
