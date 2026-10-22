<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { agent, hub } from "@/api/client";
import Badge from "@/components/Badge.vue";
import EmptyState from "@/components/EmptyState.vue";
import Icon from "@/components/Icon.vue";
import { actApply } from "@/lib/actions";
import { esc, KIND_ICON } from "@/lib/format";
import { aiRequest } from "@/stores/aiRequest";
import { useCluster } from "@/stores/cluster";
import { useSession } from "@/stores/session";
import { openAction, openDetail, toast } from "@/stores/ui";

const s = useSession(), c = useCluster();
const cfg = ref<any>(null), namespaces = ref<string[]>([]);
const running = ref(false), elapsed = ref(0), error = ref(""), result = ref<any>(null), applied = ref<any>({});
const req = aiRequest.req;
let timer = 0;
onMounted(async () => {
  try { cfg.value = await hub.get("/settings/ai"); } catch (e: any) { error.value = e.message; }
  try { namespaces.value = await agent(s.current!.id).get("namespaces"); if (!req.namespace) req.namespace = namespaces.value.find((n) => n === "default") || namespaces.value[0]; } catch { /* optional */ }
  if (aiRequest.autorun && cfg.value?.configured) { aiRequest.autorun = false; run(); }
});
const providerLabel = computed(() => (cfg.value ? `${cfg.value.provider_label} · ${cfg.value.model}` : ""));
async function run() {
  running.value = true; error.value = ""; result.value = null; applied.value = {}; elapsed.value = 0;
  timer = window.setInterval(() => elapsed.value++, 1000);
  try { result.value = await hub.post(`/clusters/${s.current!.id}/ai/diagnose`, { ...req }, 240000); }
  catch (e: any) { error.value = e.message; }
  finally { running.value = false; clearInterval(timer); }
}
const RISK: Record<string, [string, string]> = { low: ["good", "Low risk"], medium: ["warning", "Medium risk"], high: ["critical", "High risk"] };
const SEV: Record<string, string> = { critical: "critical", warning: "warning", info: "neutral" };
const TITLE: Record<string, (a: any) => string> = {
  scale: (a) => `Scale ${a.namespace}/${a.name} to ${a.replicas}`, restart: (a) => `Restart ${a.namespace}/${a.name}`,
  set_image: (a) => `Set image of ${a.namespace}/${a.name} to ${a.image}`, rollback: (a) => `Roll back ${a.namespace}/${a.name}${a.revision ? ` to revision ${a.revision}` : ""}`,
  delete_pod: (a) => `Delete pod ${a.namespace}/${a.name}`, apply_yaml: () => "Apply YAML",
};
const PERM: Record<string, string> = { scale: "workloads.scale", restart: "workloads.restart", set_image: "workloads.update_image", rollback: "workloads.rollback", delete_pod: "pods.delete", apply_yaml: "apply.yaml" };
const KUBECTL: Record<string, (a: any) => string> = {
  scale: (a) => `kubectl -n ${a.namespace} scale deploy/${a.name} --replicas=${a.replicas}`, restart: (a) => `kubectl -n ${a.namespace} rollout restart deploy/${a.name}`,
  set_image: (a) => `kubectl -n ${a.namespace} set image deploy/${a.name} ${a.container || "<container>"}=${a.image}`,
  rollback: (a) => `kubectl -n ${a.namespace} rollout undo deploy/${a.name}${a.revision ? ` --to-revision=${a.revision}` : ""}`,
  delete_pod: (a) => `kubectl -n ${a.namespace} delete pod ${a.name}`, apply_yaml: (a) => `kubectl apply -f - <<'EOF'\n${a.yaml}\nEOF`,
};
const canApply = (a: any) => !a.blocked && c.control && s.can(PERM[a.type]);
function apply(i: number) {
  const a = result.value.actions[i], done = () => { applied.value[i] = true; };
  if (a.type === "apply_yaml") { actApply(a.yaml, "Apply YAML (AI suggestion)", `<b>Suggested by AI.</b> ${esc(a.reason)} You can edit the YAML. Run <b>Dry run</b> first.`, done); return; }
  const ns = a.namespace, n = a.name;
  const dep = (c.data?.deployments || []).find((d: any) => d.namespace === ns && d.name === n);
  const reqs: Record<string, (dry: boolean) => any> = {
    scale: (dry) => ({ path: `deployments/${ns}/${n}/scale`, body: { replicas: a.replicas, dry_run: dry } }),
    restart: (dry) => ({ path: `deployments/${ns}/${n}/restart`, body: { dry_run: dry } }),
    set_image: (dry) => ({ path: `deployments/${ns}/${n}/image`, body: { container: a.container || (dep?.containers?.length === 1 ? dep.containers[0].name : ""), image: a.image, dry_run: dry } }),
    rollback: (dry) => ({ path: `deployments/${ns}/${n}/rollback`, body: { revision: a.revision || null, dry_run: dry } }),
    delete_pod: () => ({ method: "DELETE", path: `pods/${ns}/${n}` }),
  };
  openAction({ title: TITLE[a.type](a), desc: `<b>Suggested by AI.</b> ${esc(a.reason)} Run <b>Dry run</b> first.`, danger: a.type === "delete_pod" || a.risk === "high",
    confirmLabel: "Apply", request: (_m, dry) => reqs[a.type](dry), after: done });
}
async function copy(a: any) { try { await navigator.clipboard.writeText(KUBECTL[a.type](a)); toast(true, "kubectl command copied", "copy"); } catch { toast(false, "Copy failed"); } }
function md(text: string) {
  const inline = (t: string) => esc(t).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  const out: string[] = []; let list: string[] | null = null;
  for (const raw of String(text || "").split("\n")) {
    const line = raw.trimEnd(), li = line.match(/^\s*[-*]\s+(.*)/);
    if (li) { (list ||= []).push(`<li>${inline(li[1])}</li>`); continue; }
    if (list) { out.push(`<ul>${list.join("")}</ul>`); list = null; }
    if (line.trim()) out.push(`<p>${inline(line.replace(/^#+\s+/, ""))}</p>`);
  }
  if (list) out.push(`<ul>${list.join("")}</ul>`);
  return out.join("");
}
</script>

<template>
  <div class="ai-grid">
    <div style="display:flex;flex-direction:column;gap:16px;min-width:0">
      <section class="card"><div class="card-head"><h2>What should the AI look at?</h2></div>
        <div class="card-body ai-form">
          <div v-if="req.scope === 'object'"><span class="ai-target"><Icon :name="KIND_ICON[req.kind || ''] || 'pods'" />{{ req.kind }} {{ req.namespace ? `${req.namespace}/` : "" }}{{ req.name }}
            <button class="btn icon-only ghost sm" aria-label="Clear target" @click="req.scope = 'cluster'"><Icon name="x" /></button></span></div>
          <div v-else class="toolbar" style="margin:0">
            <div class="segmented" role="group" aria-label="Scope"><button :aria-pressed="req.scope === 'cluster'" @click="req.scope = 'cluster'">Whole cluster</button>
              <button :aria-pressed="req.scope === 'namespace'" @click="req.scope = 'namespace'">One namespace</button></div>
            <select v-if="req.scope === 'namespace'" v-model="req.namespace" class="select" aria-label="Namespace"><option v-for="n in namespaces" :key="n">{{ n }}</option></select>
          </div>
          <textarea v-model="req.question" placeholder="Optional: add a question, e.g. &quot;why is the worker restarting?&quot;" />
          <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
            <button class="btn primary" :disabled="running || !cfg?.configured" @click="run"><Icon name="ai" /><span>Investigate</span></button>
            <span class="hint">{{ cfg?.configured ? `Uses ${providerLabel}. Usually takes 10–60 seconds.` : "An administrator must add an AI provider key under System settings." }}</span>
          </div>
        </div></section>

      <section v-if="running" class="card"><div class="ai-running"><span class="spinner" /><div><b>Investigating…</b><div class="hint">Reading events, specs and logs. {{ elapsed }}s</div></div></div></section>
      <div v-else-if="error" class="banner show"><Icon name="crit" /><span class="msg">{{ error }}</span><button class="btn" @click="run">Retry</button></div>
      <template v-else-if="result">
        <section class="card"><div class="card-head"><h2>Summary</h2><div v-if="result.incomplete" class="right"><Badge level="warning" label="Incomplete" /></div></div>
          <div class="card-body ai-md" v-html="md(result.summary)" /></section>
        <section class="card flush"><div class="card-head"><h2>Issues</h2><span class="sub">{{ result.issues.length }}</span></div>
          <div class="card-body">
            <EmptyState v-if="!result.issues.length" title="No issues found" icon="ok" good />
            <div v-for="(i, k) in result.issues" :key="k" class="ai-issue"><div class="ttl"><Badge :level="SEV[i.severity] || 'neutral'" :label="i.severity || 'info'" />{{ i.title }}<span v-if="i.resource" class="ns">{{ i.resource }}</span></div>
              <div>{{ i.root_cause }}</div><div v-if="i.evidence" class="ev">{{ i.evidence }}</div>
              <div v-if="i.resource && i.resource.split('/').length === 3"><button class="link" @click="openDetail(i.resource.split('/')[0], i.resource.split('/')[1], i.resource.split('/')[2])">Open {{ i.resource.split("/")[0] }}</button></div></div>
          </div></section>
        <section class="card flush"><div class="card-head"><h2>Proposed fixes</h2><span class="sub">{{ result.actions.length ? `${result.actions.length} · nothing has been changed yet` : "none" }}</span></div>
          <div class="card-body">
            <div v-if="!result.actions.length" class="hint" style="padding:0 18px 16px">The AI did not propose any change.</div>
            <div v-for="(a, i) in result.actions" :key="i" class="ai-action" :class="{ applied: applied[i] }">
              <div class="ttl"><Icon :name="{ scale: 'scale', restart: 'refresh', set_image: 'tag', rollback: 'rollback', delete_pod: 'trash', apply_yaml: 'upload' }[a.type as string] || 'dot'" />{{ TITLE[a.type](a) }}
                <Badge :level="(RISK[a.risk] || RISK.medium)[0]" :label="(RISK[a.risk] || RISK.medium)[1]" /><Badge v-if="applied[i]" level="good" label="Applied" /></div>
              <div style="color:var(--text-2)">{{ a.reason }}</div>
              <pre v-if="a.type === 'apply_yaml'">{{ a.yaml }}</pre>
              <div v-if="a.blocked" class="callout"><Icon name="lock" /><span>Not allowed here: {{ a.blocked }}.</span></div>
              <div class="row">
                <button v-if="canApply(a) && !applied[i]" class="btn primary sm" @click="apply(Number(i))">Review &amp; apply…</button>
                <span v-else-if="!a.blocked && !applied[i]" class="hint">You can't apply this here (read-only agent or missing permission). Run it yourself:</span>
                <button class="btn ghost sm" @click="copy(a)"><Icon name="copy" /><span>Copy kubectl</span></button>
              </div>
            </div>
          </div></section>
        <details class="ai-steps"><summary>Investigation: {{ result.steps.length }} tool call(s) · {{ result.provider }}/{{ result.model }} · {{ result.seconds }}s · {{ (result.usage.input_tokens + result.usage.output_tokens).toLocaleString() }} tokens</summary>
          <ol><li v-for="(st, k) in result.steps" :key="k">{{ st.tool }}({{ Object.entries(st.args || {}).map(([x, y]) => `${x}=${typeof y === "string" ? y : JSON.stringify(y)}`).join(", ") }})</li></ol></details>
      </template>
    </div>
    <aside style="display:flex;flex-direction:column;gap:16px">
      <section class="card"><div class="card-head"><h2>AI provider</h2><div v-if="s.canGlobal('settings.manage')" class="right"><RouterLink class="btn sm" to="/system">Settings</RouterLink></div></div>
        <div class="card-body">
          <div v-if="!cfg" class="hint">{{ error || "Loading…" }}</div>
          <div v-else class="kv" style="grid-template-columns:1fr">
            <div><span>Status</span><b><Badge :level="cfg.configured ? 'good' : 'warning'" :label="cfg.configured ? 'Ready' : 'Not configured'" /></b></div>
            <div><span>Provider</span><b>{{ cfg.provider_label }}</b></div><div><span>Model</span><b style="font-family:var(--mono);font-size:13px">{{ cfg.model || "–" }}</b></div>
          </div></div></section>
      <section class="card"><div class="card-body hint" style="line-height:1.6">
        <b style="color:var(--text-2)">How it works.</b> The hub's AI reads problems, events, specs and logs with read-only tools, then proposes fixes.
        <b style="color:var(--text-2)">Nothing changes until you click Apply</b>, which needs the matching permission and goes through dry run, protected namespaces and the audit log.
        Secret values are never read or sent to the provider.</div></section>
    </aside>
  </div>
</template>
