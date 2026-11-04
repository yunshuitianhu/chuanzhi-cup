<script setup>
import { onMounted, onBeforeUnmount, ref } from 'vue'

const surface = ref(null)
const ready = ref(false)
let disposed = false
let motion
let mountEffect
let cleanup = () => {}

function mount() {
  cleanup()
  cleanup = () => {}
  ready.value = false
  if (disposed || !surface.value || !mountEffect) return
  try {
    cleanup = mountEffect(surface.value, value => { ready.value = value }) || (() => {})
  } catch {
    // The CSS dot field remains usable when WebGL2 cannot be initialized.
    ready.value = false
    surface.value.querySelector('canvas')?.remove()
  }
}
onMounted(async () => {
  motion = window.matchMedia('(prefers-reduced-motion: reduce)')
  motion.addEventListener('change', mount)
  try {
    const effect = await import('../utils/patternWaves.js')
    if (disposed) return
    mountEffect = effect.mountPatternWaves
    mount()
  } catch { ready.value = false }
})
onBeforeUnmount(() => {
  disposed = true
  motion?.removeEventListener('change', mount)
  cleanup()
})
</script>

<template>
  <div class="workspace-background" :class="{ ready }" aria-hidden="true">
    <div class="pattern-fallback"></div>
    <div ref="surface" class="pattern-surface"></div>
  </div>
</template>

<style scoped>
.workspace-background{position:fixed;inset:0;z-index:-1;pointer-events:none;background:#000;overflow:hidden}
.pattern-fallback,.pattern-surface{position:absolute;inset:0}
.pattern-fallback{background:radial-gradient(circle,#06b6d440 .7px,transparent 1px) 0 0/10px 10px;mask-image:radial-gradient(ellipse,#000,transparent 82%)}
.pattern-surface{opacity:0;transition:opacity .7s}.ready .pattern-surface{opacity:1}.ready .pattern-fallback{visibility:hidden}
.pattern-surface :deep(canvas){display:block;width:100%;height:100%}
@media(prefers-reduced-motion:reduce){.pattern-surface{transition:none}}
</style>
