<script setup lang="ts">
// Password / secret field with a show-hide toggle. Extra attributes (id, autocomplete, placeholder…) go to the <input>.
import { ref } from "vue";
import Icon from "./Icon.vue";
defineOptions({ inheritAttrs: false });
const model = defineModel<string>({ default: "" });
defineProps<{ mono?: boolean }>();
const emit = defineEmits<{ capslock: [on: boolean] }>();
const shown = ref(false);
function key(e: KeyboardEvent) { if (typeof e.getModifierState === "function") emit("capslock", e.getModifierState("CapsLock")); }
</script>
<template>
  <div class="pw-field">
    <input v-bind="$attrs" v-model="model" class="input" :type="shown ? 'text' : 'password'" :style="mono ? 'font-family:var(--mono)' : undefined"
      spellcheck="false" autocapitalize="off" @keyup="key" @keydown="key">
    <button type="button" class="pw-toggle" :aria-label="shown ? 'Hide password' : 'Show password'" :title="shown ? 'Hide' : 'Show'"
      :aria-pressed="shown" @click="shown = !shown"><Icon :name="shown ? 'eye-off' : 'eye'" /></button>
  </div>
</template>
