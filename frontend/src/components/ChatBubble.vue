<script setup>
import ReportEvidence from './ReportEvidence.vue'
import SafeMarkdown from './SafeMarkdown.vue'
import { ref } from 'vue'
defineProps({
  role: { type: String, required: true },
  content: { type: String, default: '' },
  incomplete: Boolean,
  evidence: {type:Object,default:null},
})
const LABEL = { user: '我', assistant: 'Agent', tool: '工具调用', error: '错误' }
const emit = defineEmits(['rendered'])
const showSource = ref(false)
</script>

<template>
  <div class="row" :class="role">
    <div class="avatar">{{ LABEL[role]?.[0] || '?' }}</div>
    <div class="bubble">
      <div class="who">{{ LABEL[role] || role }}</div>
      <div v-if="incomplete" class="who">未完成输出，请以重新分析后的结果为准</div>
      <template v-if="role==='assistant'">
        <button class="source-toggle" :aria-pressed="showSource" @click="showSource=!showSource">{{ showSource ? '排版阅读' : '查看原文' }}</button>
        <div v-if="showSource" class="text">{{ content }}</div>
        <SafeMarkdown v-else :content="content" @rendered="emit('rendered')" />
      </template>
      <div v-else class="text">{{ content }}</div>
      <ReportEvidence v-if="role==='assistant' && !incomplete && evidence?.id" :key="evidence.id" :id="evidence.id" />
    </div>
  </div>
</template>

<style scoped>
.row { display: flex; gap: 10px; align-items: flex-start; }
.row.user { flex-direction: row-reverse; }

.avatar {
  width: 30px; height: 30px; border-radius: 8px; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  font-size: 13px; font-weight: 700;
  background: var(--c-surface-2); color: var(--c-sub); border: 1px solid var(--c-border);
}
.row.user .avatar { background: var(--c-accent); color: #06121c; border-color: var(--c-accent); }
.row.tool .avatar { background: #3a2a10; color: var(--sev-mild); border-color: #6b4a10; }
.row.error .avatar { background: #3a1620; color: #ff6b81; border-color: #7b2740; }

.bubble {
  max-width: 88%; min-width:0; background: var(--c-surface-2);
  border: 1px solid var(--c-border); border-radius: 10px; padding: 9px 13px;
}
.row.user .bubble { background: #12303f; border-color: #1d5b7a; }
.row.tool .bubble, .row.error .bubble { background: transparent; }
.row.error .bubble { border-color: #7b2740; color: #ffb3c1; }

.who { font-size: 11px; color: var(--c-sub); margin-bottom: 3px; }
.text { font-size: 13px; white-space: pre-wrap; word-break: break-word; line-height: 1.7; }
.row.assistant .bubble { flex:1; }
.source-toggle { float:right; margin:0 0 6px 10px; background:transparent; color:var(--c-sub); border:1px solid var(--c-border); border-radius:5px; padding:3px 8px; font-size:11px; }
@media(max-width:600px) { .row{gap:6px}.avatar{width:24px;height:24px}.bubble{max-width:calc(100% - 30px);padding:9px}.row.assistant .bubble{max-width:calc(100% - 30px)} }
</style>
