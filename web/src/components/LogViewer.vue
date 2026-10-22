<script setup lang="ts">
// Pod or workload logs: a snapshot (last N lines) or a live stream that follows new lines, like kubectl logs -f.
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { agent, qs } from "@/api/client";
import { useSession } from "@/stores/session";
import Icon from "./Icon.vue";

const props = defineProps<{ path: string; containers?: string[]; workload?: boolean; label?: string }>();
const s = useSession();
const MAX_LINES = 5000;
const container = ref(props.containers?.[0] || ""), tail = ref(props.workload ? 50 : 200), previous = ref(false), wrap = ref(true);
const live = ref(true), paused = ref(false), filter = ref(""), stick = ref(true);
const lines = ref<string[]>([]), status = ref<"loading" | "live" | "ended" | "error" | "idle">("loading"), error = ref("");
const pre = ref<HTMLElement | null>(null);
let ctrl: AbortController | null = null, lastAt = 0, retry = 0, held: string[] = [];

watch(() => props.containers, (c) => { if (c?.length && !c.includes(container.value)) container.value = c[0]; });

function push(batch: string[]) {
  if (paused.value) { held.push(...batch); if (held.length > MAX_LINES) held = held.slice(-MAX_LINES); return; }
  const next = lines.value.concat(batch);
  lines.value = next.length > MAX_LINES ? next.slice(-MAX_LINES) : next;
  if (stick.value) nextTick(() => { if (pre.value) pre.value.scrollTop = pre.value.scrollHeight; });
}
function stop() { ctrl?.abort(); ctrl = null; clearTimeout(retry); }

async function snapshot() {
  stop(); status.value = "loading"; error.value = ""; lines.value = [];
  try {
    const text = await agent(s.current!.id).text(props.path, { container: container.value, tail: tail.value, previous: previous.value });
    push(text ? text.replace(/\n$/, "").split("\n") : []); status.value = "idle";
  } catch (e: any) { status.value = "error"; error.value = previous.value ? `No previous container logs: ${e.message}` : e.message; }
}

async function follow(resume = false) {
  stop();
  const my = (ctrl = new AbortController());
  if (!resume) { lines.value = []; held = []; }
  status.value = resume ? status.value : "loading"; error.value = "";
  const since = resume && lastAt ? Math.max(1, Math.ceil((Date.now() - lastAt) / 1000)) : undefined;
  const query = { container: props.workload ? undefined : container.value, tail: resume ? 0 : tail.value, since_seconds: since };
  try {
    const r = await fetch(`/api/hub/clusters/${s.current!.id}/api/${props.path}/stream${qs(query)}`, { signal: my.signal, credentials: "same-origin" });
    if (!r.ok || !r.body) {
      let msg = `${r.status}`;
      try { msg = (await r.json()).detail || msg; } catch { /* not json */ }
      throw new Error(msg);
    }
    status.value = "live";
    const reader = r.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split("\n"); buf = parts.pop() || "";
      if (parts.length) { lastAt = Date.now(); push(parts); }
    }
    if (buf) push([buf]);
    if (my === ctrl) { status.value = "ended"; retry = window.setTimeout(() => live.value && follow(true), 3000); }
  } catch (e: any) {
    if (e.name === "AbortError" || my !== ctrl) return;
    status.value = "error"; error.value = e.message;
    retry = window.setTimeout(() => live.value && follow(true), 5000);
  }
}

function reload() { if (live.value && !previous.value) follow(); else snapshot(); }
watch([container, tail, previous, live], () => { if (previous.value) live.value = false; reload(); }, { immediate: true });
watch(() => props.path, reload);
watch(paused, (p) => { if (!p && held.length) { const h = held; held = []; push(h); } });
onBeforeUnmount(stop);

function onScroll() { const el = pre.value; if (el) stick.value = el.scrollHeight - el.scrollTop - el.clientHeight < 30; }
const shown = computed(() => { const q = filter.value.trim().toLowerCase(); return q ? lines.value.filter((l) => l.toLowerCase().includes(q)) : lines.value; });
const POD_COLORS = ["var(--series-1)", "var(--series-2)", "var(--series-3, #b46cd6)", "var(--series-4, #d6913a)", "var(--series-5, #3aa79a)"];
const podColor = new Map<string, string>();
function split(line: string): [string, string, string] {
  const m = props.workload ? /^\[([^\]]+)\] (.*)$/.exec(line) : null;
  if (!m) return ["", "", line];
  if (!podColor.has(m[1])) podColor.set(m[1], POD_COLORS[podColor.size % POD_COLORS.length]);
  return [m[1], podColor.get(m[1])!, m[2]];
}
const copied = ref(false);
async function copy() { try { await navigator.clipboard.writeText(shown.value.join("\n")); copied.value = true; setTimeout(() => (copied.value = false), 1500); } catch { /* denied */ } }
function download() {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([lines.value.join("\n") + "\n"], { type: "text/plain" }));
  a.download = `${(props.label || props.path).replace(/[^\w.-]+/g, "_")}.log`; a.click(); URL.revokeObjectURL(a.href);
}
const STATUS: Record<string, string> = { loading: "Connecting…", live: "Live", ended: "Reconnecting…", error: "Disconnected", idle: "Snapshot" };
</script>

<template>
  <div class="logs-bar">
    <div class="segmented" role="group" aria-label="Mode">
      <button :aria-pressed="live" :disabled="previous" @click="live = true"><span class="live-dot" :class="{ on: live && status === 'live' }" />Live</button>
      <button :aria-pressed="!live" @click="live = false">Snapshot</button></div>
    <select v-if="!workload && containers && containers.length > 1" v-model="container" class="select" aria-label="Container"><option v-for="x in containers" :key="x">{{ x }}</option></select>
    <select v-model.number="tail" class="select" aria-label="Lines"><option v-for="n in (workload ? [10, 50, 200, 500] : [100, 200, 500, 1000, 5000])" :key="n" :value="n">{{ live ? `Start with last ${n}` : `Last ${n} lines` }}</option></select>
    <label v-if="!workload"><input v-model="previous" type="checkbox"> Previous container</label>
    <label><input v-model="wrap" type="checkbox"> Wrap</label>
    <label class="search sm"><Icon name="search" /><input v-model="filter" class="input" placeholder="Filter lines" aria-label="Filter lines"></label>
    <span style="flex:1" />
    <span class="hint" :style="{ color: status === 'error' ? 'var(--critical)' : undefined }">{{ STATUS[status] }} · {{ shown.length }}{{ filter ? ` of ${lines.length}` : "" }} lines</span>
    <button v-if="live" class="btn" :aria-pressed="paused" @click="paused = !paused"><Icon :name="paused ? 'play' : 'pause'" /><span>{{ paused ? "Resume" : "Pause" }}</span></button>
    <button class="btn" @click="copy"><Icon name="copy" /><span>{{ copied ? "Copied" : "Copy" }}</span></button>
    <button class="btn" title="Download" aria-label="Download logs" @click="download"><Icon name="upload" style="transform:rotate(180deg)" /></button>
    <button class="btn" @click="lines = []">Clear</button>
    <button class="btn" @click="reload"><Icon name="refresh" /><span>Reload</span></button>
  </div>
  <pre ref="pre" class="logs" :class="{ wrap }" tabindex="0" :aria-label="label || 'Logs'" aria-live="off" @scroll="onScroll"><template v-if="error && !lines.length">{{ error }}</template><template v-else-if="!lines.length">{{ status === "loading" ? "Loading logs…" : live ? "Waiting for new log lines…" : "(no log output)" }}</template><template v-else v-for="(l, i) in shown" :key="i"><template v-if="workload"><span class="log-pod" :style="{ color: split(l)[1] }">{{ split(l)[0] ? split(l)[0] + " " : "" }}</span>{{ split(l)[2] }}
</template><template v-else>{{ l }}
</template></template></pre>
  <div v-if="error && lines.length" class="hint" style="color:var(--critical);margin-top:6px">{{ error }}</div>
  <button v-if="!stick && lines.length" class="btn sm" style="margin-top:6px" @click="stick = true; pre && (pre.scrollTop = pre.scrollHeight)">↓ Jump to latest</button>
</template>
