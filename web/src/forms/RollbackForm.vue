<script setup lang="ts">
import { onMounted, ref } from "vue";
import { agent } from "@/api/client";
import { useSession } from "@/stores/session";
const props = defineProps<{ model: Record<string, any> }>();
const history = ref<any[] | null>(null);
const error = ref("");
onMounted(async () => {
  try {
    const h = await agent(useSession().current!.id).get(`deployments/${props.model.namespace}/${props.model.name}/history`);
    history.value = h;
    const older = h.filter((r: any) => !r.current);
    if (older.length) props.model.revision = older[0].revision;
  } catch (e: any) { error.value = e.message; }
});
</script>
<template>
  <div v-if="error" class="act-result show err">{{ error }}</div>
  <div v-else-if="!history" class="hint">Loading revisions…</div>
  <div v-else-if="!history.some((r) => !r.current)" class="callout">No earlier revision exists yet. Update the image first, then you can roll back.</div>
  <template v-else>
    <p class="hint" style="margin:0">Current: revision #{{ history.find((r) => r.current)?.revision }} · {{ history.find((r) => r.current)?.images.join(", ") }}</p>
    <div class="radio-list" role="radiogroup">
      <label v-for="(r, i) in history.filter((x) => !x.current)" :key="r.revision">
        <input v-model="model.revision" type="radio" name="rb" :value="r.revision">
        <div><div class="main-line">Revision #{{ r.revision }}<span v-if="i === 0" class="dim" style="font-weight:400"> · previous</span></div>
          <div class="sub-line">{{ r.images.join(", ") }}</div></div>
      </label>
    </div>
  </template>
</template>
