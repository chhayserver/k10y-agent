<script setup lang="ts">
// Roadmap board. Edit the lists below to update the page; no dates are promised for future items.
import { computed, ref } from "vue";

interface Item { t: string; d: string; area: string; v?: string }
const columns: { id: string; title: string; hint: string; items: Item[] }[] = [
  { id: "next", title: "Next up", hint: "Being designed or built now", items: [
    { t: "HAProxy management", d: "Monitor, drain and configure HAProxy with validated changes, live charts and logs, certificates, rate limits, config history and rollback, rolling deploys, groups, alerts and a Kubernetes sync. Built, in testing.", area: "Control" },
    { t: "Two-factor login (TOTP)", d: "Authenticator-app codes for sign-in, with an option to require them for admins.", area: "Security" },
    { t: "More alert channels", d: "Slack, email and generic webhooks next to Telegram.", area: "Alerts" },
    { t: "Silence and acknowledge alerts", d: "Mute a firing alert for a while, and show who acknowledged it.", area: "Alerts" },
    { t: "Alerts for certificate and storage issues", d: "Expiring TLS Secrets, PVCs stuck in Pending, and full volumes.", area: "Alerts" },
  ] },
  { id: "planned", title: "Planned", hint: "Agreed, not started", items: [
    { t: "Single sign-on (OIDC)", d: "Sign in with Google, Microsoft Entra ID, Keycloak or any OIDC provider, mapping groups to roles.", area: "Security" },
    { t: "Long-term metrics", d: "Read history from Prometheus so charts go beyond the last hour.", area: "Monitoring" },
    { t: "Helm chart", d: "Install the agent with Helm, and run the hub inside Kubernetes.", area: "Install" },
    { t: "Audit log export", d: "Ship audit entries to a SIEM or object storage, with retention settings.", area: "Security" },
    { t: "CI on every change", d: "Automated tests, type checks and image builds for every pull request.", area: "Project" },
  ] },
  { id: "exploring", title: "Exploring", hint: "Ideas we're researching. Tell us if you need them", items: [
    { t: "Port-forward from the browser", d: "Reach a Service or Pod port through the hub without kubectl.", area: "Control" },
    { t: "Cost view", d: "Requests vs. real usage per namespace, to spot over-provisioned workloads.", area: "Monitoring" },
    { t: "GitOps awareness", d: "Show when an object is managed by Argo CD or Flux, and warn before editing it.", area: "Control" },
    { t: "Scheduled reports", d: "A daily or weekly cluster health summary to Telegram or email.", area: "Alerts" },
    { t: "Translations", d: "The dashboard in more languages.", area: "Project" },
  ] },
];

const shipped: { v: string; date: string; items: string[] }[] = [
  { v: "2.1", date: "Oct 2026", items: ["Pod shell in the browser", "Edit YAML with diff review", "Sessions and device sign-out", "Settings saved to your account", "Automatic backups", "API-only agents", "New sign-in page"] },
  { v: "2.0", date: "Oct 2026", items: ["Hub with PostgreSQL", "Users and custom roles", "HTTPS everywhere", "Alerts to Telegram", "Live log streaming", "CronJobs, autoscalers, Ingresses, storage pages", "Vue 3 + TypeScript dashboard"] },
  { v: "1.5", date: "Oct 2026", items: ["Namespaces page", "Personal settings"] },
  { v: "1.4", date: "Oct 2026", items: ["StatefulSets, DaemonSets and Jobs"] },
  { v: "1.3", date: "Oct 2026", items: ["Details panel for every resource"] },
  { v: "1.2", date: "Oct 2026", items: ["AI assistant (Gemini, OpenAI-compatible)", "Two providers, switch with one click"] },
  { v: "1.1", date: "Oct 2026", items: ["One-command install with kubectl apply"] },
  { v: "1.0", date: "Oct 2026", items: ["Monitoring, control with guardrails, map", "Multi-cluster, live charts, multi-arch image"] },
];

const areas = ["All", ...new Set(columns.flatMap((c) => c.items.map((i) => i.area)))];
const area = ref("All");
const visible = computed(() => columns.map((c) => ({ ...c, items: c.items.filter((i) => area.value === "All" || i.area === area.value) })));
const repo = "https://github.com/zorouzumaki1111-cmd/k10y-agent";
</script>

<template>
  <div class="rm">
    <div class="filters" role="group" aria-label="Filter by area">
      <button v-for="a in areas" :key="a" :class="{ on: area === a }" :aria-pressed="area === a" @click="area = a">{{ a }}</button>
    </div>
    <div class="board">
      <section v-for="c in visible" :key="c.id" class="col" :class="c.id">
        <header><span class="dot" /><h2>{{ c.title }}</h2><span class="n">{{ c.items.length }}</span></header>
        <p class="hint">{{ c.hint }}</p>
        <article v-for="i in c.items" :key="i.t" class="item"><span class="area">{{ i.area }}</span><h3>{{ i.t }}</h3><p>{{ i.d }}</p></article>
        <p v-if="!c.items.length" class="empty">Nothing here for this area yet.</p>
      </section>
    </div>

    <h2 class="sh">Shipped</h2>
    <ol class="timeline">
      <li v-for="s in shipped" :key="s.v"><div class="ver"><b>v{{ s.v }}</b><span>{{ s.date }}</span></div>
        <ul><li v-for="i in s.items" :key="i">{{ i }}</li></ul></li>
    </ol>

    <div class="ask">
      <div><h3>Missing something?</h3><p>Open an issue with your use case. Requests with a clear problem behind them move up fastest.</p></div>
      <a class="btn" :href="`${repo}/issues/new`" target="_blank" rel="noopener">Suggest a feature</a>
    </div>
  </div>
</template>

<style scoped>
.rm { margin-top: 24px; }
.filters { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 20px; }
.filters button { padding: 6px 14px; border-radius: 999px; border: 1px solid var(--vp-c-divider); font-size: 14px; color: var(--vp-c-text-2); }
.filters button.on { background: var(--vp-c-brand-soft); border-color: var(--vp-c-brand-2); color: var(--vp-c-brand-1); font-weight: 600; }
.board { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; align-items: start; }
.col { padding: 16px; border-radius: 14px; background: var(--vp-c-bg-soft); border: 1px solid var(--vp-c-divider); }
.col header { display: flex; align-items: center; gap: 8px; }
.col h2 { margin: 0 !important; padding: 0 !important; border: 0 !important; font-size: 17px !important; flex: 1; }
.col .n { font-size: 12px; font-weight: 700; padding: 1px 8px; border-radius: 999px; background: var(--vp-c-default-soft); }
.dot { width: 10px; height: 10px; border-radius: 50%; }
.next .dot { background: #f2b33d; } .planned .dot { background: #4f62e8; } .exploring .dot { background: #8a8fa3; }
.hint { margin: 4px 0 12px !important; font-size: 13px; color: var(--vp-c-text-3); }
.item { padding: 14px; border-radius: 10px; background: var(--vp-c-bg); border: 1px solid var(--vp-c-divider); margin-top: 10px; }
.item h3 { margin: 6px 0 4px !important; font-size: 15px !important; }
.item p { margin: 0 !important; font-size: 13.5px; line-height: 1.5; color: var(--vp-c-text-2); }
.area { font-size: 11.5px; font-weight: 650; color: var(--vp-c-brand-1); background: var(--vp-c-brand-soft); padding: 2px 8px; border-radius: 999px; }
.empty { font-size: 13px; color: var(--vp-c-text-3); }
.sh { margin-top: 48px !important; }
.timeline { list-style: none; padding: 0 !important; margin: 16px 0 0 !important; border-left: 2px solid var(--vp-c-divider); }
.timeline > li { position: relative; padding: 0 0 22px 24px; margin: 0 !important; }
.timeline > li::before { content: ""; position: absolute; left: -7px; top: 6px; width: 12px; height: 12px; border-radius: 50%; background: #22c58b; box-shadow: 0 0 0 4px var(--vp-c-bg); }
.ver { display: flex; align-items: baseline; gap: 10px; } .ver b { font-size: 17px; } .ver span { font-size: 13px; color: var(--vp-c-text-3); }
.timeline ul { margin: 6px 0 0 !important; padding-left: 18px; } .timeline ul li { margin: 2px 0 !important; font-size: 14.5px; color: var(--vp-c-text-2); }
.ask { display: flex; flex-wrap: wrap; gap: 16px; align-items: center; justify-content: space-between; margin-top: 32px; padding: 22px 24px; border-radius: 14px;
  background: linear-gradient(135deg, rgba(59, 140, 240, .12), rgba(91, 78, 224, .12)); border: 1px solid var(--vp-c-divider); }
.ask h3 { margin: 0 0 4px !important; } .ask p { margin: 0 !important; color: var(--vp-c-text-2); }
.btn { padding: 10px 18px; border-radius: 999px; background: linear-gradient(135deg, #3b8cf0, #5b4ee0); color: #fff !important; font-weight: 600; text-decoration: none !important; white-space: nowrap; }
@media (max-width: 900px) { .board { grid-template-columns: 1fr; } }
</style>
