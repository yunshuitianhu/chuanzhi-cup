<script setup>
defineProps({quote:{type:Object,required:true},removable:Boolean})
defineEmits(['remove'])
</script>
<template><section class="text-quote-card" aria-label="待解释文字引用"><header><strong>{{quote.title||'所选文字'}}</strong><button v-if="removable" type="button" aria-label="移除文字引用" @click="$emit('remove')">移除</button></header><blockquote>{{quote.text}}</blockquote><p>选中文字仅作待解释材料；发送时核对来源与版本。</p></section></template>
<style scoped>
.text-quote-card { font-size:12px; line-height:1.8; border:1px solid var(--c-border); border-left:3px solid var(--c-accent); padding:13px 14px; background:var(--c-bg); border-radius:var(--radius-sm); margin:10px 0 16px; overflow-wrap:anywhere; min-width:0; }
header { display:flex; align-items:flex-start; justify-content:space-between; gap:10px; }
strong { font-size:13px; font-weight:600; }
button { font:inherit; min-height:24px; padding:0 3px; color:var(--c-sub); background:transparent; border:0; flex-shrink:0; }
button:hover { color:var(--c-ink); }
blockquote { margin:12px 0 8px; padding:8px 0; white-space:pre-wrap; max-height:180px; overflow:auto; font-size:13px; line-height:1.85; }
p { color:var(--c-sub); font-size:11px; margin:6px 0 0; }
</style>
