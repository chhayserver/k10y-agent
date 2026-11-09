<script setup lang="ts">
// TLS certificates Nginx uses (every ssl_certificate), with days left and the sites that use each.
import { onMounted, ref } from "vue";
import Badge from "@/components/Badge.vue";
import Icon from "@/components/Icon.vue";
import { ng } from "@/lib/nginx";
import { useNginx } from "@/stores/nginx";

const n = useNginx();
const list = ref<any[] | null>(null), error = ref("");
onMounted(async () => { try { list.value = await ng.certificates(n.currentId!); } catch (e: any) { error.value = e.message; } });
const level = (c: any) => (c.error ? "critical" : c.days_left < 0 ? "critical" : c.days_left < 14 ? "warning" : "good");
const label = (c: any) => (c.error ? "Can't read" : c.days_left < 0 ? `Expired ${-c.days_left} d ago` : `${c.days_left} days left`);
</script>

<template>
  <div v-if="error" class="banner show"><Icon name="crit" /><span class="msg">{{ error }}</span></div>
  <section class="card flush"><div class="card-body table-wrap">
    <table class="data"><thead><tr><th>Expiry</th><th>Domains</th><th>File</th><th>Issuer</th><th>Used by</th><th>Valid until</th></tr></thead>
      <tbody><tr v-for="c in list || []" :key="c.path">
        <td><Badge :level="level(c)" :label="label(c)" /><div v-if="c.error" class="dim" style="font-size:12px">{{ c.error }}</div></td>
        <td class="mono" style="white-space:normal">{{ (c.dns_names || []).join(", ") || "–" }}</td>
        <td class="mono dim">{{ c.path }}</td><td>{{ c.issuer || "–" }}</td>
        <td>{{ c.sites.map((s: any) => s.server_names.join(", ")).join("; ") }}</td>
        <td class="dim">{{ c.not_after ? new Date(c.not_after).toLocaleDateString() : "–" }}</td></tr>
        <tr v-if="list && !list.length"><td colspan="6" class="dim">No site uses TLS (no ssl_certificate in the config).</td></tr></tbody></table>
  </div></section>
  <p class="hint" style="margin-top:12px">Read from the files each <code>ssl_certificate</code> points to (never the key). Alerts can warn before one expires.</p>
</template>
