<script setup lang="ts">
// Interactive shell in a container, through the hub (WebSocket). Every session is audited.
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { nextTick, onBeforeUnmount, ref, watch } from "vue";
import { isDark } from "@/stores/prefs";
import { shell } from "@/stores/ui";
import Badge from "./Badge.vue";
import Icon from "./Icon.vue";

const host = ref<HTMLElement | null>(null);
const status = ref<"connecting" | "open" | "closed">("connecting"), note = ref("");
let term: Terminal | null = null, fit: FitAddon | null = null, ws: WebSocket | null = null, ro: ResizeObserver | null = null;

const CLOSE: Record<number, string> = { 4401: "Your session expired. Sign in again.", 4403: "Not allowed", 4404: "Cluster not found", 1011: "The shell could not start." };

function theme() {
  return isDark()
    ? { background: "#0d1117", foreground: "#e6edf3", cursor: "#58a6ff", selectionBackground: "#264f78" }
    : { background: "#fbfbfa", foreground: "#1f2328", cursor: "#2a78d6", selectionBackground: "#c8ddf7" };
}

function sendResize() {
  if (ws?.readyState === WebSocket.OPEN && term) ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
}

function connect() {
  const r = shell.current!;
  ws?.close();
  term!.reset();
  status.value = "connecting"; note.value = "";
  term!.writeln(`\x1b[2mConnecting to ${r.clusterName} · ${r.namespace}/${r.pod}${r.container ? ` (${r.container})` : ""}…\x1b[0m`);
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const q = r.container ? `?container=${encodeURIComponent(r.container)}` : "";
  const sock = (ws = new WebSocket(`${proto}://${location.host}/api/hub/clusters/${r.clusterId}/exec/${encodeURIComponent(r.namespace)}/${encodeURIComponent(r.pod)}${q}`));
  sock.onopen = () => { status.value = "open"; fit!.fit(); sendResize(); term!.focus(); };
  sock.onmessage = (e) => term!.write(typeof e.data === "string" ? e.data : "");
  sock.onclose = (e) => {
    if (sock !== ws) return;
    status.value = "closed";
    note.value = e.reason || CLOSE[e.code] || "";
    term!.writeln(`\r\n\x1b[2m[session closed${note.value ? `: ${note.value}` : ""}]\x1b[0m`);
  };
}

async function open() {
  await nextTick();
  if (!host.value) return;
  term = new Terminal({ cursorBlink: true, fontFamily: 'ui-monospace, "SF Mono", Menlo, Consolas, monospace', fontSize: 13, scrollback: 5000, theme: theme(), allowProposedApi: false });
  fit = new FitAddon();
  term.loadAddon(fit);
  term.open(host.value);
  fit.fit();
  term.onData((data) => { if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "input", data })); });
  term.onResize(sendResize);
  ro = new ResizeObserver(() => { try { fit?.fit(); } catch { /* hidden */ } });
  ro.observe(host.value);
  connect();
}

function dispose() { ws?.close(); ws = null; ro?.disconnect(); term?.dispose(); term = null; }
function close() { dispose(); shell.current = null; }
function switchContainer(c: string) { shell.current!.container = c; connect(); }
function onKey(e: KeyboardEvent) { if (e.key === "Escape" && e.shiftKey) close(); }

watch(() => shell.current?.pod + "/" + shell.current?.namespace + "/" + shell.current?.clusterId, (k, old) => {
  if (!shell.current) { dispose(); return; }
  if (k !== old) { dispose(); open(); }
}, { immediate: true });
onBeforeUnmount(dispose);
</script>
<template>
  <div v-if="shell.current" class="shell-backdrop" @keydown="onKey">
    <section class="shell-win" role="dialog" aria-modal="true" :aria-label="`Shell in ${shell.current.pod}`">
      <header class="shell-head">
        <Icon name="code" />
        <div class="shell-title"><b>{{ shell.current.pod }}</b><span>{{ shell.current.clusterName }} · {{ shell.current.namespace }}</span></div>
        <select v-if="shell.current.containers.length > 1" class="select sm" :value="shell.current.container" aria-label="Container"
          @change="switchContainer(($event.target as HTMLSelectElement).value)"><option v-for="c in shell.current.containers" :key="c">{{ c }}</option></select>
        <Badge :level="status === 'open' ? 'good' : status === 'connecting' ? 'pending' : 'neutral'" :label="status === 'open' ? 'Connected' : status === 'connecting' ? 'Connecting' : 'Closed'" />
        <span style="flex:1" />
        <span class="hint shell-tip">Shift+Esc to close · recorded in Activity</span>
        <button v-if="status === 'closed'" class="btn sm" @click="connect"><Icon name="refresh" /><span>Reconnect</span></button>
        <button class="btn icon-only ghost" aria-label="Close shell" @click="close"><Icon name="x" /></button>
      </header>
      <div ref="host" class="shell-term" />
    </section>
  </div>
</template>
