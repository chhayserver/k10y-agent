<script setup lang="ts">
import Icon from "@/components/Icon.vue";
import { DETAIL_KINDS, KIND_ICON } from "@/lib/format";
import { openDetail } from "@/stores/ui";
defineProps<{ kind: string; namespace?: string | null; name: string; label?: string; extra?: string | null; tab?: string }>();
</script>
<template>
  <button v-if="DETAIL_KINDS.has(kind)" class="map-chip" :title="`${kind} ${name}`" @click="openDetail(kind, namespace, name, { push: true, tab })">
    <Icon :name="KIND_ICON[kind] || 'dot'" /><span>{{ label || name }}</span><i v-if="extra">{{ extra }}</i></button>
  <span v-else class="map-chip"><Icon :name="KIND_ICON[kind] || 'dot'" /><span>{{ label || name }}</span></span>
</template>
