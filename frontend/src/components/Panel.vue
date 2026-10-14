<script setup>
/** 内容卡片。title/subtitle 为页头，actions 插槽放刷新等操作按钮。 */
defineProps({
  title: { type: String, default: '' },
  subtitle: { type: String, default: '' },
})
</script>

<template>
  <section class="card">
    <header v-if="title" class="head">
      <div>
        <h2 class="title">{{ title }}</h2>
        <p v-if="subtitle" class="sub">{{ subtitle }}</p>
      </div>
      <div class="actions"><slot name="actions" /></div>
    </header>
    <div class="body"><slot /></div>
  </section>
</template>

<style scoped>
.card {
  background: var(--c-surface); border: 1px solid var(--c-border);
  border-radius: 10px; margin-bottom: 16px; overflow: hidden;
}
.head {
  display: flex; align-items: flex-start; justify-content: space-between; gap: 12px;
  padding: 14px 18px; border-bottom: 1px solid var(--c-border);
  background: var(--c-surface-2);
}
.title { margin: 0; font-size: 15px; font-weight: 600; }
.sub { margin: 3px 0 0; font-size: 12px; color: var(--c-sub); }
.actions { flex-shrink: 0; }
.body { padding: 16px 18px; }
</style>
