<script setup>
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { mountTargetCursor } from '../utils/targetCursor.js'

const root = ref(null)
const corners = ref([])
const dot = ref(null)
let query
let cleanup = () => {}

function sync() {
  cleanup()
  cleanup = () => {}
  if (root.value) root.value.style.opacity = '0'
  if (!query.matches || !root.value || !dot.value) return
  cleanup = mountTargetCursor(root.value, corners.value, dot.value)
}
onMounted(() => {
  query = window.matchMedia('(hover: hover) and (pointer: fine) and (prefers-reduced-motion: no-preference)')
  query.addEventListener('change', sync)
  sync()
})
onBeforeUnmount(() => { query?.removeEventListener('change', sync); cleanup() })
</script>

<template>
  <Teleport to="body">
    <div ref="root" class="target-cursor" aria-hidden="true">
      <span v-for="angle in [0, 90, 180, 270]" :key="angle" ref="corners" class="target-cursor__corner"></span>
      <span ref="dot" class="target-cursor__dot"></span>
    </div>
  </Teleport>
</template>

<style scoped>
.target-cursor{position:fixed;inset:0;z-index:2147483647;pointer-events:none;opacity:0}
.target-cursor__corner{position:absolute;top:0;left:0;box-sizing:border-box;border-style:solid;border-color:var(--target-cursor-color,#d0f4f0);transform-origin:0 0;will-change:transform}
.target-cursor__dot{position:absolute;top:0;left:0;border-radius:50%;background:var(--target-cursor-color,#d0f4f0);will-change:transform}
@media(hover:none),(pointer:coarse),(prefers-reduced-motion:reduce){.target-cursor{display:none}}
</style>
