<script setup lang="ts">
// A CronJob's recent runs (the Jobs it created): result, start, duration; and whether it's healthy.
import { onMounted, ref } from "vue";
import { agent } from "@/api/client";
import Badge from "@/components/Badge.vue";
import Icon from "@/components/Icon.vue";
import { fmt } from "@/lib/format";
import { useSession } from "@/stores/session";
import OpenLink from "./OpenLink.vue";

const props = defineProps<{ namespace: string; name: string }>();
const s = useSession();
const d = ref<any>(null), error = ref("");
onMounted(async () => { try { d.value = await agent(s.current!.id).get(`cronjobs/${props.namespace}/${props.name}/runs`); } catch (e: any) { error.value = e.message; } });
const LEVEL: Record<string, string> = { Complete: "good", Failed: "critical", Running: "warning", Suspended: "neutral" };
const dur = (sec: number | null) => (sec == null ? "–" : sec < 60 ? `${sec}s` : sec < 3600 ? `${Math.floor(sec / 60)}m ${sec % 60}s` : `${(sec / 3600).toFixed(1)}h`);
</script>

<template>
  <div>
    <h3 class="section-title">Runs</h3>
    <div v-if="error" class="test-result show err"><Icon name="crit" /><span>{{ error }}</span></div>
    <template v-else-if="d">
      <div class="test-result show" :class="d.problem ? 'err' : 'ok'" style="margin-bottom:10px"><Icon :name="d.problem ? 'crit' : 'ok'" />
        <span>{{ d.problem ? `Unhealthy: ${d.problem}` : d.cronjob.suspended ? "Suspended" : "Healthy" }} · last run {{ d.cronjob.last_schedule_s != null ? `${fmt.age(d.cronjob.last_schedule_s)} ago` : "never" }}
          · last success {{ d.cronjob.last_success_s != null ? `${fmt.age(d.cronjob.last_success_s)} ago` : "never" }}</span></div>
      <p v-if="!d.runs.length" class="dim" style="margin:0">No runs kept (Kubernetes keeps the last few successful and failed Jobs).</p>
      <div v-else class="table-wrap card" style="box-shadow:none"><table class="data"><thead><tr><th>Job</th><th>Result</th><th>Started</th><th>Duration</th><th>Reason</th></tr></thead>
        <tbody><tr v-for="j in d.runs" :key="j.name"><td class="primary"><OpenLink kind="Job" :namespace="j.namespace" :name="j.name" /></td>
          <td><Badge :level="LEVEL[j.status] || 'neutral'" :label="j.status" /></td><td class="dim">{{ j.age_s != null ? `${fmt.age(j.age_s)} ago` : "–" }}</td>
          <td class="num">{{ dur(j.duration_s) }}</td><td>{{ j.failure_reason || "–" }}</td></tr></tbody></table></div>
    </template>
    <p v-else class="hint">Loading runs…</p>
  </div>
</template>
