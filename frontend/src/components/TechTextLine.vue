<script setup>
import { onMounted, onBeforeUnmount, ref } from 'vue'

const props = defineProps({ text: { type: String, required: true } })
const layer = ref(null)
const canvas = ref(null)
const ready = ref(false)
let disposed = false
let disposeEffect = () => {}
let motion
const stopForReducedMotion = () => { if (motion?.matches) { disposeEffect(); ready.value = false } }

onMounted(async () => {
  motion = window.matchMedia('(prefers-reduced-motion: reduce)')
  motion.addEventListener('change', stopForReducedMotion)
  if (motion.matches) return
  try {
    const { mountTechText } = await import('../utils/techText.js')
    if (disposed || motion.matches || !layer.value || !canvas.value) return
    disposeEffect = mountTechText(layer.value, canvas.value, props.text, value => { ready.value = value }) || (() => {})
  } catch {
    ready.value = false
  }
})
onBeforeUnmount(() => {
  disposed = true
  motion?.removeEventListener('change', stopForReducedMotion)
  disposeEffect()
})
</script>

<template>
  <span class="tech-line" :class="{ ready }">
    <span class="tech-fallback">{{ text }}</span>
    <span ref="layer" class="tech-layer" aria-hidden="true"><canvas ref="canvas"></canvas></span>
  </span>
</template>

<style scoped>
.tech-line{display:inline-block;position:relative;vertical-align:top}
.tech-fallback{transition:opacity .12s}.ready>.tech-fallback{opacity:0}
.tech-layer{position:absolute;inset:-16px;opacity:0;pointer-events:none;touch-action:pan-y}
.ready>.tech-layer{opacity:1;pointer-events:auto}
canvas{display:block;width:100%;height:100%}
@media(prefers-reduced-motion:reduce){.tech-fallback{opacity:1!important;transition:none}.tech-layer{display:none}}
</style>
