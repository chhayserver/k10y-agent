<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { withBase } from "vitepress";

const repo = "https://github.com/zorouzumaki1111-cmd/k10y-agent";

// Hero: real screenshots of the product, switchable like n8n's workflow tabs.
const demos = [
  { id: "overview", label: "Live health", icon: "M3 12h4l3-8 4 16 3-8h4", img: "/shots/overview.png", url: "k10y / lab / overview",
    text: "Health, problems, CPU and memory for every cluster, updated live." },
  { id: "logs", label: "Live logs", icon: "M4 6h16M4 12h10M4 18h13", img: "/shots/logs.png", url: "k10y / lab / deployments / web / logs",
    text: "Follow the logs of every pod of a workload in one stream, tagged by pod." },
  { id: "shell", label: "Pod shell", icon: "M4 17l6-5-6-5M12 19h8", img: "/shots/shell.png", url: "k10y / lab / pods / shell",
    text: "Open a terminal in any container from the browser. Every session is audited." },
  { id: "yaml", label: "Edit YAML", icon: "M8 6l-6 6 6 6M16 6l6 6-6 6", img: "/shots/yaml.png", url: "k10y / lab / configmaps / app-config",
    text: "Edit like kubectl edit, review a diff, dry run, then save." },
  { id: "alerts", label: "Alerts", icon: "M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.9 1.9 0 0 0 3.4 0", img: "/shots/alert-rule.png", url: "k10y / alerts / rules",
    text: "Rules for broken workloads, CPU and memory, and unreachable clusters, sent to Telegram." },
  { id: "map", label: "Map", icon: "M9 4 3 6v14l6-2 6 2 6-2V4l-6 2-6-2zM9 4v14M15 6v14", img: "/shots/map.png", url: "k10y / lab / map",
    text: "See how Ingresses, Services, workloads, Pods and config connect." },
];
const active = ref(0);
let timer = 0, userPicked = false;
function pick(i: number) { active.value = i; userPicked = true; }
onMounted(() => {
  timer = window.setInterval(() => { if (!userPicked && !document.hidden) active.value = (active.value + 1) % demos.length; }, 6500);
});
onBeforeUnmount(() => clearInterval(timer));

const install = [
  { title: "1 · Start the hub", note: "Docker Compose: hub + PostgreSQL + automatic backups", cmd: `curl -LO ${repo}/releases/latest/download/docker-compose.yml\nmake hub-up   # or: create .env, then docker compose up -d` },
  { title: "2 · Add each cluster", note: "One manifest; the agent creates its own API token", cmd: `kubectl apply -f ${repo}/releases/latest/download/install.yaml` },
];
const copied = ref(-1);
async function copy(i: number) {
  try { await navigator.clipboard.writeText(install[i].cmd); copied.value = i; setTimeout(() => (copied.value = -1), 1500); } catch { /* blocked */ }
}

const worksWith = ["Kubernetes", "RKE2", "k3s", "Amazon EKS", "Google GKE", "Azure AKS", "Docker Compose", "PostgreSQL", "Telegram", "Google Gemini", "OpenAI-compatible APIs", "amd64 + arm64"];
const stats = [
  { n: "2", l: "commands to install" }, { n: "38", l: "pages in the dashboard" }, { n: "24", l: "fine-grained permissions" },
  { n: "Every", l: "change audited, with who made it" }, { n: "0", l: "kubeconfigs handed out" },
];
const useCases = [
  { t: "Run many clusters from one screen", d: "Lab, staging and production side by side, with a health dot for each. When one cluster is down, the rest keep working.", tag: "Platform teams" },
  { t: "Give developers access without kubeconfigs", d: "A role per cluster: view and logs on production, scale and restart on staging. Developers never hold credentials, and every click is audited.", tag: "Safe self-service" },
  { t: "Get alerted, then fix it from your phone", d: "A crash loop fires a Telegram message, with an optional AI diagnosis. Open k10y, check the live logs, roll back. Done.", tag: "On-call" },
  { t: "Let AI explain a CrashLoopBackOff", d: "The assistant reads events, specs and logs with read-only tools, finds the root cause and proposes a fix. A person reviews it before anything changes.", tag: "Troubleshooting" },
];
const features = [
  { kicker: "See", title: "See + understand", img: "/shots/overview.png", url: "k10y / lab / overview", points: [
    ["Live charts", "CPU and memory for the cluster, every node and every pod, with request and limit lines."],
    ["Problems first", "Crash loops, failing image pulls, pending pods, NotReady nodes and failed jobs, before you go looking."],
    ["Every resource", "Deployments to PVCs, CronJobs to autoscalers, each with a details panel, related objects, YAML and events."]] },
  { kicker: "Control", title: "Control + break nothing", img: "/shots/yaml-panel.png", url: "k10y / lab / configmaps / app-config", reverse: true, points: [
    ["Dry run everything", "Scale, restart, roll back, drain, apply or edit YAML: see the result before it happens."],
    ["Guardrails", "Protected namespaces, typed confirmation for destructive actions, and conflict checks when editing."],
    ["Least privilege", "Read-only installs are one manifest. Write access is opt-in and can't create RBAC or CRDs."]] },
  { kicker: "Respond", title: "Respond + fix fast", img: "/shots/logs-panel.png", url: "k10y / lab / deployments / web / logs", points: [
    ["Alerts that wait", "A hold time keeps blips quiet. Reminders, resolved messages, and history in PostgreSQL."],
    ["Live logs and shell", "Follow every pod of a workload at once, or open a shell in a container. Both in the browser."],
    ["AI assistant", "Gemini or any OpenAI-compatible model (optional; cluster data goes to the provider you choose). Read-only tools; you approve every fix."]] },
];
const security = [
  { t: "Identity", items: ["argon2id passwords, forced first change", "Server-side sessions, sign out any device", "Login rate limiting"] },
  { t: "Authorization", items: ["Fully custom roles", "Per-cluster or global assignments", "24 fine-grained permissions"] },
  { t: "Data", items: ["HTTPS everywhere, no Ingress needed", "Tokens and keys encrypted at rest", "Secret values never leave the cluster"] },
  { t: "Accountability", items: ["Every action, login and shell audited", "Automatic database backups", "API-only agents, no side doors"] },
];
</script>

<template>
  <div class="k-home">
    <!-- HERO -->
    <section class="hero">
      <div class="hero-grid" aria-hidden="true" /><div class="hero-glow" aria-hidden="true" />
      <div class="wrap">
        <a class="pill" :href="withBase('/changelog')"><span class="dot" />New in 2.1: pod shell, YAML editing, sessions <span aria-hidden="true">→</span></a>
        <h1>Every cluster.<br><span class="grad">One calm place.</span></h1>
        <p class="lead">k10y-agent is an open-source control room for Kubernetes. Monitor, troubleshoot and safely change every cluster from one dashboard, with roles, audit, alerts and an AI assistant built in.</p>
        <div class="ctas">
          <a class="btn primary" :href="withBase('/docs/quick-start')">Get started, it's free</a>
          <a class="btn ghost" :href="repo" target="_blank" rel="noopener"><svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M12 .5a12 12 0 0 0-3.8 23.4c.6.1.8-.3.8-.6v-2c-3.3.7-4-1.6-4-1.6-.6-1.4-1.4-1.8-1.4-1.8-1-.7.1-.7.1-.7 1.2.1 1.8 1.2 1.8 1.2 1 1.8 2.8 1.3 3.5 1 .1-.8.4-1.3.7-1.6-2.7-.3-5.5-1.3-5.5-6 0-1.3.5-2.4 1.2-3.2-.1-.3-.5-1.5.1-3.2 0 0 1-.3 3.3 1.2a11.5 11.5 0 0 1 6 0C17.3 4.7 18.3 5 18.3 5c.7 1.7.2 2.9.1 3.2.8.8 1.2 1.9 1.2 3.2 0 4.6-2.8 5.6-5.5 5.9.4.4.8 1.1.8 2.2v3.3c0 .3.2.7.8.6A12 12 0 0 0 12 .5z"/></svg>Star on GitHub</a>
        </div>
        <p class="sub">Self-hosted · Free and open source · Your cluster data stays on your servers</p>

        <div class="demo">
          <div class="tabs" role="tablist" aria-label="Product tour">
            <button v-for="(d, i) in demos" :key="d.id" role="tab" :aria-selected="active === i" :class="{ on: active === i }" @click="pick(i)">
              <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path :d="d.icon" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></svg>{{ d.label }}</button>
          </div>
          <div class="frame">
            <div class="bar"><span /><span /><span /><em>{{ demos[active].url }}</em></div>
            <div class="screens">
              <img v-for="(d, i) in demos" :key="d.id" :src="withBase(d.img)" :alt="`${d.label}: ${d.text}`" :class="{ on: active === i }"
                :loading="i === 0 ? 'eager' : 'lazy'" decoding="async">
            </div>
          </div>
          <p class="caption">{{ demos[active].text }}</p>
        </div>
      </div>
    </section>

    <!-- WORKS WITH -->
    <section class="works wrap">
      <p>Works with the Kubernetes you already run</p>
      <ul><li v-for="w in worksWith" :key="w">{{ w }}</li></ul>
    </section>

    <!-- STATS -->
    <section class="stats wrap" aria-label="k10y in numbers">
      <div v-for="s in stats" :key="s.l"><b>{{ s.n }}</b><span>{{ s.l }}</span></div>
    </section>

    <!-- INSTALL -->
    <section class="install wrap" id="install">
      <div class="sec-head"><span class="kicker">Get started</span><h2>Two commands. <span class="grad">Five minutes.</span></h2>
        <p>Run the hub anywhere Docker runs, then add a small agent to each cluster. No Ingress, no Helm charts, no agents phoning home.</p></div>
      <div class="cmds">
        <div v-for="(c, i) in install" :key="i" class="cmd">
          <div class="cmd-head"><b>{{ c.title }}</b><span>{{ c.note }}</span>
            <button class="copy" :aria-label="`Copy ${c.title}`" @click="copy(i)">{{ copied === i ? "Copied" : "Copy" }}</button></div>
          <pre><code>{{ c.cmd }}</code></pre>
        </div>
      </div>
      <p class="more"><a :href="withBase('/docs/quick-start')">Read the quick start →</a></p>
    </section>

    <!-- USE CASES -->
    <section class="cases wrap" id="use-cases">
      <div class="sec-head"><span class="kicker">Use cases</span><h2>Built for the people <span class="grad">who keep clusters running</span></h2></div>
      <div class="case-grid">
        <article v-for="u in useCases" :key="u.t" class="case"><span class="tag">{{ u.tag }}</span><h3>{{ u.t }}</h3><p>{{ u.d }}</p></article>
      </div>
    </section>

    <!-- FEATURES -->
    <section class="features" id="features">
      <div v-for="f in features" :key="f.title" class="feature wrap" :class="{ reverse: f.reverse }">
        <div class="f-text">
          <span class="kicker">{{ f.kicker }}</span>
          <h2>{{ f.title.split(" + ")[0] }} <span class="grad">+ {{ f.title.split(" + ")[1] }}</span></h2>
          <dl><template v-for="[t, d] in f.points" :key="t"><dt>{{ t }}</dt><dd>{{ d }}</dd></template></dl>
        </div>
        <div class="f-img">
          <div class="frame small"><div class="bar"><span /><span /><span /><em>{{ f.url }}</em></div><img :src="withBase(f.img)" :alt="f.title" loading="lazy" decoding="async"></div>
        </div>
      </div>
    </section>

    <!-- FEATURE GRID -->
    <section class="grid wrap">
      <div class="sec-head"><span class="kicker">Everything included</span><h2>One tool instead of <span class="grad">five tabs</span></h2></div>
      <div class="cards">
        <div class="card"><h3>Multi-cluster</h3><p>A cluster switcher with live health dots, and an all-clusters overview.</p></div>
        <div class="card"><h3>HAProxy too</h3><p>Monitor and configure HAProxy load balancers next to your clusters, with validated changes and a live topology.</p></div>
        <div class="card"><h3>Relationship map</h3><p>Ingress → Service → workload → Pod → ConfigMap, Secret or PVC. Broken links are flagged.</p></div>
        <div class="card"><h3>CronJobs</h3><p>Run now, suspend and resume, with readable schedules like "every 5 minutes".</p></div>
        <div class="card"><h3>Per-user settings</h3><p>Hide pages and set your theme and start page. Your settings follow you to every device.</p></div>
        <div class="card"><h3>CLI included</h3><p><code>k10y status --all</code>, logs, scale, roll back and apply, against any agent.</p></div>
      </div>
    </section>

    <!-- SECURITY -->
    <section class="sec">
      <div class="wrap">
        <div class="sec-head light"><span class="kicker">Security</span><h2>Secure by default, <span class="grad">not by checklist</span></h2>
          <p>k10y sits between people and your clusters, so it was designed to be the safest way in.</p></div>
        <div class="sec-grid">
          <div v-for="s in security" :key="s.t"><h3>{{ s.t }}</h3><ul><li v-for="i in s.items" :key="i">{{ i }}</li></ul></div>
        </div>
        <p class="more"><a :href="withBase('/security')">How k10y keeps your clusters safe →</a></p>
      </div>
    </section>

    <!-- ROADMAP TEASER -->
    <section class="road wrap">
      <div class="sec-head"><span class="kicker">Roadmap</span><h2>Built in the open, <span class="grad">shipping fast</span></h2>
        <p>Nine releases so far, from the first agent to multi-cluster, the AI assistant, the hub, alerts and pod shells.</p></div>
      <div class="road-cols">
        <div><h3><span class="b done" />Shipped</h3><ul><li>Hub with roles and audit</li><li>Alerts to Telegram</li><li>Pod shell and YAML editing</li><li>Live logs</li></ul></div>
        <div><h3><span class="b now" />Next</h3><ul><li>Two-factor login (TOTP)</li><li>Slack, email and webhook alerts</li><li>Alert silencing</li></ul></div>
        <div><h3><span class="b later" />Later</h3><ul><li>Single sign-on (OIDC)</li><li>Long-term metrics (Prometheus)</li><li>Helm chart</li></ul></div>
      </div>
      <p class="more"><a :href="withBase('/roadmap')">See the full roadmap →</a></p>
    </section>

    <!-- FINAL CTA -->
    <section class="final">
      <div class="wrap">
        <h2>Bring calm to your clusters <span class="grad">today</span></h2>
        <p>Free, self-hosted and running in five minutes.</p>
        <div class="ctas center"><a class="btn primary" :href="withBase('/docs/quick-start')">Get started</a>
          <a class="btn ghost" :href="withBase('/docs/')">Read the docs</a></div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.k-home { --wrap: 1180px; overflow-x: clip; }
.wrap { max-width: var(--wrap); margin: 0 auto; padding: 0 24px; }
.grad { background: linear-gradient(90deg, #6fa6ff, #a28bff 60%, #6be3b5); -webkit-background-clip: text; background-clip: text; color: transparent; }
.kicker { display: inline-block; font-size: 13px; font-weight: 650; letter-spacing: .08em; text-transform: uppercase; color: var(--vp-c-brand-1); margin-bottom: 10px; }
h2 { margin: 0; font-size: clamp(30px, 4vw, 46px); line-height: 1.08; letter-spacing: -0.03em; font-weight: 750; border: 0; padding: 0; }
.btn { display: inline-flex; align-items: center; gap: 8px; height: 46px; padding: 0 22px; border-radius: 999px; font-weight: 600; font-size: 15px; text-decoration: none !important; transition: transform .15s, filter .15s, background .15s; }
.btn.primary { background: var(--k-grad); color: #fff; box-shadow: 0 10px 30px -10px rgba(91, 78, 224, .8); }
.btn.primary:hover { filter: brightness(1.08); transform: translateY(-1px); }
.btn.ghost { color: #fff; border: 1px solid rgba(255,255,255,.22); background: rgba(255,255,255,.04); }
.btn.ghost:hover { background: rgba(255,255,255,.1); }
.more { margin-top: 26px; } .more a { font-weight: 600; text-decoration: none; }
.sec-head { max-width: 760px; margin-bottom: 36px; }
.sec-head p { margin: 14px 0 0; font-size: 17px; line-height: 1.6; color: var(--vp-c-text-2); }

/* hero */
.hero { position: relative; isolation: isolate; color: #fff; padding: 72px 0 40px; text-align: center;
  background: radial-gradient(1200px 600px at 50% -10%, #1c2f7a 0%, transparent 60%), linear-gradient(180deg, #08102a 0%, #0b1530 60%, var(--vp-c-bg) 100%); }
.hero-grid { position: absolute; inset: 0; z-index: -1; opacity: .5; background-image: linear-gradient(rgba(255,255,255,.05) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.05) 1px, transparent 1px);
  background-size: 44px 44px; mask-image: radial-gradient(ellipse at 50% 20%, #000 15%, transparent 65%); }
.hero-glow { position: absolute; z-index: -1; width: 700px; height: 400px; left: 50%; top: 280px; transform: translateX(-50%); border-radius: 50%;
  background: radial-gradient(closest-side, rgba(91, 78, 224, .5), transparent); filter: blur(30px); }
.pill { display: inline-flex; align-items: center; gap: 8px; padding: 6px 14px; border-radius: 999px; font-size: 13.5px; color: #cdd6ff !important;
  border: 1px solid rgba(160, 175, 255, .25); background: rgba(80, 100, 230, .12); text-decoration: none !important; }
.pill:hover { background: rgba(80, 100, 230, .22); }
.pill .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--k-green); box-shadow: 0 0 0 4px rgba(34, 197, 139, .2); }
.hero h1 { margin: 22px 0 0; font-size: clamp(42px, 7vw, 78px); line-height: 1.02; letter-spacing: -0.045em; font-weight: 800; }
.lead { max-width: 720px; margin: 22px auto 0; font-size: clamp(16px, 2vw, 19px); line-height: 1.6; color: rgba(255,255,255,.75); }
.ctas { display: flex; gap: 12px; justify-content: center; flex-wrap: wrap; margin-top: 30px; }
.sub { margin: 16px 0 0; font-size: 13.5px; color: rgba(255,255,255,.5); }
.demo { margin-top: 52px; }
.tabs { display: inline-flex; flex-wrap: wrap; justify-content: center; gap: 6px; padding: 6px; border-radius: 14px; background: rgba(255,255,255,.06); border: 1px solid rgba(255,255,255,.08); }
.tabs button { display: inline-flex; align-items: center; gap: 7px; padding: 8px 14px; border-radius: 10px; font-size: 14px; font-weight: 550; color: rgba(255,255,255,.7); transition: background .15s, color .15s; }
.tabs button:hover { color: #fff; background: rgba(255,255,255,.06); }
.tabs button.on { color: #fff; background: var(--k-grad); box-shadow: 0 6px 18px -8px rgba(91, 78, 224, .9); }
.frame { margin: 22px auto 0; border-radius: 16px; overflow: hidden; border: 1px solid rgba(255,255,255,.12); background: #111318;
  box-shadow: 0 50px 100px -40px rgba(30, 50, 160, .7), 0 0 0 8px rgba(255,255,255,.03); text-align: left; }
.bar { display: flex; align-items: center; gap: 6px; padding: 11px 14px; background: #1b1d24; border-bottom: 1px solid rgba(255,255,255,.06); }
.bar span { width: 10px; height: 10px; border-radius: 50%; background: #ff5f57; } .bar span:nth-child(2) { background: #febc2e; } .bar span:nth-child(3) { background: #28c840; }
.bar em { margin-left: 12px; font-style: normal; font-size: 12px; color: #8b90a0; font-family: var(--vp-font-family-mono); background: #12141a; padding: 3px 12px; border-radius: 6px;
  overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.screens { display: grid; }
.screens img { grid-area: 1 / 1; width: 100%; height: auto; display: block; opacity: 0; transition: opacity .45s ease; }
.screens img.on { opacity: 1; }
.caption { margin: 18px 0 0; color: var(--vp-c-text-2); font-size: 15px; }

/* works with */
.works { text-align: center; padding-top: 40px; }
.works p { margin: 0 0 16px; font-size: 13px; letter-spacing: .08em; text-transform: uppercase; color: var(--vp-c-text-3); font-weight: 600; }
.works ul { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 10px; justify-content: center; }
.works li { padding: 7px 14px; border-radius: 999px; border: 1px solid var(--vp-c-divider); font-size: 14px; color: var(--vp-c-text-2); background: var(--vp-c-bg-soft); }

/* stats */
.stats { display: grid; grid-template-columns: repeat(5, 1fr); gap: 1px; margin-top: 56px; border-radius: 16px; overflow: hidden; background: var(--vp-c-divider); border: 1px solid var(--vp-c-divider); padding: 0; }
.stats div { background: var(--vp-c-bg); padding: 26px 20px; text-align: center; }
.stats b { display: block; font-size: 36px; line-height: 1.25; padding-bottom: 2px; font-weight: 800; letter-spacing: -0.03em; background: var(--k-grad); -webkit-background-clip: text; background-clip: text; color: transparent; }
.stats span { font-size: 14px; color: var(--vp-c-text-2); }

/* install */
.install, .cases, .grid, .road { padding-top: 110px; }
.cmds { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
.cmd { border-radius: 14px; border: 1px solid var(--vp-c-divider); background: var(--vp-c-bg-soft); overflow: hidden; }
.cmd-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 10px; padding: 16px 18px 0; }
.cmd-head b { font-size: 16px; } .cmd-head span { font-size: 13.5px; color: var(--vp-c-text-2); flex: 1; }
.copy { font-size: 13px; font-weight: 600; padding: 4px 12px; border-radius: 8px; border: 1px solid var(--vp-c-divider); color: var(--vp-c-text-1); background: var(--vp-c-bg); }
.copy:hover { border-color: var(--vp-c-brand-1); color: var(--vp-c-brand-1); }
.cmd pre { margin: 14px 0 0; padding: 16px 18px; background: #0d1117; color: #d6e2ff; font: 13.5px/1.6 var(--vp-font-family-mono); overflow-x: auto; white-space: pre; }

/* use cases */
.case-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 18px; }
.case { padding: 26px; border-radius: 16px; border: 1px solid var(--vp-c-divider); background: var(--vp-c-bg-soft); transition: border-color .2s, transform .2s; }
.case:hover { border-color: var(--vp-c-brand-2); transform: translateY(-2px); }
.case .tag { font-size: 12.5px; font-weight: 650; color: var(--vp-c-brand-1); background: var(--vp-c-brand-soft); padding: 3px 10px; border-radius: 999px; }
.case h3 { margin: 14px 0 8px; font-size: 21px; letter-spacing: -0.02em; }
.case p { margin: 0; color: var(--vp-c-text-2); line-height: 1.6; }

/* features */
.features { padding-top: 70px; }
.feature { display: grid; grid-template-columns: 0.9fr 1.3fr; gap: 56px; align-items: center; padding-top: 70px; }
.feature.reverse .f-text { order: 2; }
.f-text dl { margin: 26px 0 0; }
.f-text dt { font-weight: 650; font-size: 17px; margin-top: 18px; display: flex; align-items: center; gap: 10px; }
.f-text dt::before { content: ""; width: 8px; height: 8px; border-radius: 2px; background: var(--k-grad); transform: rotate(45deg); flex: none; }
.f-text dd { margin: 6px 0 0 18px; color: var(--vp-c-text-2); line-height: 1.6; }
.frame.small { margin: 0; box-shadow: 0 30px 70px -35px rgba(30, 50, 160, .55); }
.frame.small img { display: block; width: 100%; height: auto; }

/* grid */
.cards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 18px; }
.card { padding: 24px; border-radius: 16px; border: 1px solid var(--vp-c-divider); background: var(--vp-c-bg-soft); }
.card h3 { margin: 0 0 8px; font-size: 18px; }
.card p { margin: 0; color: var(--vp-c-text-2); line-height: 1.6; font-size: 15px; }

/* security */
.sec { margin-top: 110px; padding: 90px 0; color: #fff; background: radial-gradient(900px 400px at 85% 0%, rgba(91, 78, 224, .35), transparent 60%), #0a1330; }
.sec-head.light p { color: rgba(255,255,255,.7); }
.sec .kicker { color: #9db4ff; }
.sec-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 18px; }
.sec-grid > div { padding: 24px; border-radius: 16px; background: rgba(255,255,255,.04); border: 1px solid rgba(255,255,255,.09); }
.sec-grid h3 { margin: 0 0 12px; font-size: 17px; }
.sec-grid ul { margin: 0; padding: 0; list-style: none; display: grid; gap: 9px; }
.sec-grid li { position: relative; padding-left: 22px; color: rgba(255,255,255,.78); font-size: 14.5px; line-height: 1.45; }
.sec-grid li::before { content: "✓"; position: absolute; left: 0; color: var(--k-green); font-weight: 700; }
.sec .more a { color: #b6c6ff; }

/* roadmap teaser */
.road-cols { display: grid; grid-template-columns: repeat(3, 1fr); gap: 18px; }
.road-cols > div { padding: 24px; border-radius: 16px; border: 1px solid var(--vp-c-divider); background: var(--vp-c-bg-soft); }
.road-cols h3 { margin: 0 0 12px; font-size: 17px; display: flex; align-items: center; gap: 9px; }
.road-cols ul { margin: 0; padding-left: 18px; color: var(--vp-c-text-2); line-height: 1.9; }
.b { width: 10px; height: 10px; border-radius: 50%; } .b.done { background: var(--k-green); } .b.now { background: #f2b33d; } .b.later { background: #8a8fa3; }

/* final */
.final { margin-top: 110px; padding: 90px 0 100px; text-align: center; background: radial-gradient(700px 300px at 50% 100%, rgba(91, 78, 224, .25), transparent 70%); }
.final p { margin: 14px 0 0; font-size: 18px; color: var(--vp-c-text-2); }
.ctas.center .btn.ghost { color: var(--vp-c-text-1); border-color: var(--vp-c-divider); background: var(--vp-c-bg-soft); }

@media (max-width: 960px) {
  .stats { grid-template-columns: repeat(2, 1fr); } .stats div:last-child { grid-column: span 2; }
  .cmds, .case-grid, .feature, .road-cols { grid-template-columns: 1fr; }
  .feature { gap: 28px; } .feature.reverse .f-text { order: 0; }
  .cards, .sec-grid { grid-template-columns: 1fr 1fr; }
}
@media (max-width: 600px) {
  .cards, .sec-grid { grid-template-columns: 1fr; }
  .hero { padding-top: 48px; }
  .install, .cases, .grid, .road { padding-top: 72px; }
  .tabs button { padding: 7px 10px; font-size: 13px; }
}
@media (prefers-reduced-motion: reduce) { .screens img, .case, .btn { transition: none; } }
</style>
