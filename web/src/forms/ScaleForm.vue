<script setup lang="ts">
const props = defineProps<{ model: Record<string, any> }>();
const step = (d: number) => { props.model.replicas = Math.max(0, (Number(props.model.replicas) || 0) + d); };
</script>
<template>
  <div class="form-row">
    <label class="lbl" for="scale-n">Replicas</label>
    <div class="stepper">
      <button type="button" class="btn icon-only" aria-label="Decrease" @click="step(-1)">−</button>
      <input id="scale-n" v-model.number="model.replicas" class="input" type="number" min="0" max="1000">
      <button type="button" class="btn icon-only" aria-label="Increase" @click="step(1)">+</button>
    </div>
    <span v-if="Number(model.replicas) === 0" class="hint">Scaling to 0 stops every pod.</span>
  </div>
</template>
