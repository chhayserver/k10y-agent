<script setup lang="ts">
// Every change applied through k10y: who, when, what, the diff, and Restore (puts the files back as they were
// before that change, through the same review).
import { computed, onMounted, reactive, ref } from "vue";
import Icon from "@/components/Icon.vue";
import Modal from "@/components/Modal.vue";
import { fmt } from "@/lib/format";
import { ng } from "@/lib/nginx";
import { useNginx } from "@/stores/nginx";
import { useSession } from "@/stores/session";
import NgChangeDialog from "./NgChangeDialog.vue";

const n = useNginx(), s = useSession();
const canConfigure = computed(() => s.canNg("nginx.configure", n.currentId));
const list = ref<any[] | null>(null), error = ref("");
async function load() { try { list.value = await ng.history(n.currentId!); } catch (e: any) { error.value = e.message; } }
onMounted(load);
const view = reactive({ open: false, v: 0, diff: "", loading: false });
async function show(v: number) {
  Object.assign(view, { open: true, v, diff: "", loading: true });
  try { view.diff = (await ng.version(n.currentId!, v)).diff; } catch (e: any) { view.diff = e.message; } finally { view.loading = false; }
}
const restore = reactive({ open: false, v: 0 });
const lines = computed(() => view.diff.split("\n").filter((l) => !l.startsWith("+++") && l !== ""));
const who = (a: string) => (a || "").replace(/^hub:/, "") || "–";
</script>

<template>
  <div v-if="error" class="banner show"><Icon name="crit" /><span class="msg">{{ error }}</span></div>
  <section class="card flush"><div class="card-body table-wrap">
    <table class="data"><thead><tr><th class="num">Version</th><th>When</th><th>Who</th><th>What</th><th>Files</th><th class="actions"><span class="sr-only">Actions</span></th></tr></thead>
      <tbody><tr v-for="h in list || []" :key="h.version" class="clickable" @click="(ev) => !(ev.target as HTMLElement).closest('button') && show(h.version)">
        <td class="num"><b>{{ h.version }}</b></td><td class="dim">{{ fmt.dateTime(new Date(h.time * 1000).toISOString()) }}</td><td>{{ who(h.actor) }}</td>
        <td style="white-space:normal">{{ h.summary.join("; ") }}</td><td class="mono dim" style="white-space:normal">{{ h.files.map((f: string) => f.split("/").pop()).join(", ") }}</td>
        <td class="actions"><button class="btn ghost sm" type="button" @click="show(h.version)">Diff</button>
          <button v-if="canConfigure" class="btn ghost sm" type="button" @click="Object.assign(restore, { open: true, v: h.version })">Restore</button></td></tr>
        <tr v-if="list && !list.length"><td colspan="6" class="dim">No changes made through k10y yet. Changes made by hand on the server aren't listed here.</td></tr></tbody></table>
  </div></section>
  <p class="hint" style="margin-top:12px"><b>Restore</b> puts the files back as they were <em>before</em> that change (also undoing later changes to the same files), after the usual check.</p>
  <Modal :open="view.open" :title="`Version ${view.v}`" wide @close="view.open = false">
    <p v-if="view.loading" class="hint">Loading…</p>
    <div v-else class="ye-diff hx-diff"><div v-for="(l, i) in lines" :key="i" class="ye-line hx-line" :class="{ add: l[0] === '+', del: l[0] === '-', hunk: l.startsWith('@@') }">
      <span class="ye-op">{{ l.startsWith("@@") || l.startsWith("---") ? "" : l[0] === "-" ? "−" : l[0] === "+" ? "+" : "" }}</span><span class="ye-t">{{ l.startsWith("---") ? l.slice(4) : l.startsWith("@@") ? "⋯" : l.slice(1) }}</span></div></div>
    <template #actions><span style="flex:1" /><button class="btn" type="button" @click="view.open = false">Close</button></template>
  </Modal>
  <NgChangeDialog :open="restore.open" :title="`Restore the files from before version ${restore.v}`" :restore="restore.v" danger @close="restore.open = false" @done="restore.open = false; load()" />
</template>
