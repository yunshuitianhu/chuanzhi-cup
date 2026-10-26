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
  min-width:0;
  background: var(--c-surface); border: 1px solid var(--c-border);
  border-radius: var(--radius-md); margin-bottom: 20px; overflow: hidden;
}
.head {
  display: flex; flex-wrap:wrap; align-items: flex-start; justify-content: space-between; gap: 12px;
  padding: 18px 22px; border-bottom: 1px solid var(--c-border);
  background: var(--c-surface);
}
.title { margin: 0; font-size: 18px; font-weight: 600; }
.sub { margin: 5px 0 0; font-size: 13px; color: var(--c-sub); }
.head>div { min-width:0; }.actions { max-width:100%; }
.body { padding: 18px 22px; min-width:0; }
@media(max-width:600px){.head{padding:12px}.body{padding:12px}.title,.sub{overflow-wrap:anywhere}}
</style>
