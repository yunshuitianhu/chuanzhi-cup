<script setup>
defineProps({quote:{type:Object,required:true},removable:Boolean})
defineEmits(['remove'])
</script>
<template><section class="text-quote-card" aria-label="待解释文字引用"><header><strong>{{quote.title||'所选文字'}}</strong><button v-if="removable" type="button" aria-label="移除文字引用" @click="$emit('remove')">移除</button></header><blockquote>{{quote.text}}</blockquote><p>选中文字仅作待解释材料；发送时核对来源与版本。</p></section></template>
<style scoped>
.text-quote-card{font-size:12px;border-left:3px solid var(--c-accent);padding:10px 12px;background:var(--c-surface-2);border-radius:7px;margin:10px 0;overflow-wrap:anywhere}header{display:flex;justify-content:space-between;gap:10px}button{font:inherit;color:var(--c-sub);background:transparent;border:0}blockquote{margin:8px 0;white-space:pre-wrap;max-height:160px;overflow:auto}p{color:var(--c-sub);font-size:11px;margin:6px 0 0}
</style>
