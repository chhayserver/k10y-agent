<script setup lang="ts">
// Turn a frontend's maintenance page on: every request gets a friendly "Back soon" page (HTTP 503),
// except from the IPs you list (so your team can still test). Turning it off is one click in the menu.
import { reactive, watch } from "vue";
import Icon from "@/components/Icon.vue";
import Modal from "@/components/Modal.vue";
import type { HxFrontend, HxOp } from "@/lib/haproxy";

const props = defineProps<{ open: boolean; frontend: HxFrontend | null }>();
const emit = defineEmits<{ close: []; review: [ops: HxOp[], title: string] }>();
const f = reactive({ message: "", allow: "", err: "" });
watch(() => props.open, (o) => {
  if (!o) return;
  const m = props.frontend?.policies.maintenance;
  Object.assign(f, { message: m?.message || "", allow: (m?.allow_ips || []).join(" "), err: "" });
});
function submit() {
  const ips = f.allow.split(/[\s,]+/).filter(Boolean);
  if (ips.some((x) => !/^[0-9a-fA-F:.]+(\/\d{1,3})?$/.test(x))) { f.err = "Use IP addresses or networks like 10.0.0.0/8, separated by spaces."; return; }
  emit("review", [{ op: "update", kind: "frontend", name: props.frontend!.name, data: { policies: { ...props.frontend!.policies, maintenance: { enabled: true, message: f.message.trim(), allow_ips: ips } } } }],
    `Maintenance page on ${props.frontend!.name}`);
}
</script>

<template>
  <Modal :open="open && !!frontend" :title="`Maintenance page for ${frontend?.name}`" @close="emit('close')">
    <p class="hint" style="margin:0">Every request to <b>{{ frontend?.name }}</b> gets a simple "Back soon" page (HTTP 503) until you turn it off. Servers keep running, so you can work on them.</p>
    <div class="form-row"><label class="lbl" for="hxm-msg">Message</label>
      <input id="hxm-msg" v-model="f.message" class="input" maxlength="300" placeholder="We're upgrading our systems. We'll be back at 10:00 (UTC+7).">
      <span class="hint">Leave empty for a standard message.</span></div>
    <div class="form-row"><label class="lbl" for="hxm-allow">Still let these IPs through</label>
      <input id="hxm-allow" v-model="f.allow" class="input mono" placeholder="203.0.113.10 10.0.0.0/8">
      <span class="hint">Your office or VPN, so you can test while everyone else sees the page.</span></div>
    <div v-if="f.err" class="test-result show err"><Icon name="crit" /><span>{{ f.err }}</span></div>
    <template #actions><button class="btn ghost" type="button" @click="emit('close')">Cancel</button><span style="flex:1" />
      <button class="btn primary" type="button" @click="submit">Review change</button></template>
  </Modal>
</template>
