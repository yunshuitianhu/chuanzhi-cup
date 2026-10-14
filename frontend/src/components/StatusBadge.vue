<script setup>
defineProps({
  ok: { type: Boolean, default: false },
  text: { type: String, default: '' },
})
</script>

<template>
  <span class="badge" :class="{ ok }">
    <span class="dot"></span>{{ text }}
  </span>
</template>

<style scoped>
.badge {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 12px; color: var(--c-sub);
  padding: 4px 10px; border-radius: 20px;
  border: 1px solid var(--c-border);
}
.dot { width: 7px; height: 7px; border-radius: 50%; background: #b23c4d; }
.badge.ok .dot { background: var(--c-accent-2); box-shadow: 0 0 6px var(--c-accent-2); }
.badge.ok { color: var(--c-accent-2); border-color: #1d5b4f; }
</style>
