<script setup>
/**
 * 轻量全局提示。用法见 composables/useToast.js。
 */
import { toasts } from '../composables/useToast'
</script>

<template>
  <div class="toast-wrap">
    <TransitionGroup name="toast">
      <div v-for="t in toasts" :key="t.id" class="toast" :class="t.type">
        <span class="ico">{{ t.type === 'error' ? '✕' : t.type === 'warn' ? '!' : '✓' }}</span>
        <span>{{ t.text }}</span>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.toast-wrap {
  position: fixed; right: 20px; bottom: 20px; z-index: 9999;
  display: flex; flex-direction: column; gap: 8px; align-items: flex-end;
}
.toast {
  display: flex; align-items: center; gap: 8px;
  background: var(--c-surface-2); border: 1px solid var(--c-border);
  color: var(--c-ink); padding: 10px 16px; border-radius: 8px;
  font-size: 13px; max-width: 380px;
  box-shadow: 0 6px 24px rgba(0,0,0,.4);
}
.toast.ok    { border-color: #1d5b4f; }
.toast.error { border-color: #7b2740; }
.toast.warn  { border-color: #6b4a10; }
.ico { font-weight: 700; }
.toast.ok .ico    { color: var(--c-accent-2); }
.toast.error .ico { color: #ff6b81; }
.toast.warn .ico  { color: var(--sev-mild); }

.toast-enter-active, .toast-leave-active { transition: all .25s ease; }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translateX(20px); }
</style>
