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

.card{min-width:0;background:var(--c-surface);border:1px solid var(--c-border-soft);border-radius:var(--radius-md);margin-bottom:24px;overflow:hidden}
.head{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:14px;padding:22px 24px;border-bottom:1px solid var(--c-border-soft)}
.title{margin:0;font-size:19px;font-weight:650;letter-spacing:.02em;line-height:1.5}
.sub{margin:6px 0 0;font-size:12px;color:var(--c-sub);line-height:1.8}
.head>div{min-width:0}.actions{max-width:100%}.actions:empty{display:none}
.body{padding:22px 24px;min-width:0}
@media(max-width:600px){.card{border-radius:10px;margin-bottom:18px}.head{padding:16px;gap:12px}.body{padding:16px}.title{font-size:18px}.title,.sub{overflow-wrap:anywhere}}

</style>
