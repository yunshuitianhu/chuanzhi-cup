<script setup>
import QuoteText from './QuoteText.vue'
import ReportEvidence from './ReportEvidence.vue'
import QuestionContextCard from './QuestionContextCard.vue'
import SafeMarkdown from './SafeMarkdown.vue'
import { ref } from 'vue'
defineProps({
  role: { type: String, required: true },
  content: { type: String, default: '' },
  incomplete: Boolean,
  evidence: {type:Object,default:null},
  textSource: {type:Object,default:null},
  questionContext: {type:Object,default:null},
})
const LABEL = { user: '我', assistant: '分析助手', tool: '工具调用', error: '错误' }
const emit = defineEmits(['rendered'])
const showSource = ref(false)
</script>

<template>
  <div class="row" :class="role">
    <div class="avatar">{{ LABEL[role]?.[0] || '?' }}</div>
    <div class="bubble">
      <div class="bubble-head"><div class="who">{{ LABEL[role] || role }}</div><button v-if="role==='assistant'" class="source-toggle" :aria-pressed="showSource" @click="showSource=!showSource">{{ showSource ? '排版阅读' : '查看原文' }}</button></div>
      <div v-if="incomplete" class="who">未完成输出，请以重新分析后的结果为准</div>
      <QuoteText v-if="role==='assistant'" :source="textSource?{...textSource,view:showSource?'raw':'markdown'}:null" title="回答中的文字" :source-id="textSource?.message_id?'message-'+textSource.message_id:''">
        <div v-if="showSource" class="text">{{ content }}</div>
        <SafeMarkdown v-else :content="content" @rendered="emit('rendered')" />
      </QuoteText>
      <div v-else class="text">{{ content }}</div>
      <QuestionContextCard v-if="questionContext" :context="questionContext" compact />
      <ReportEvidence v-if="role==='assistant' && !incomplete && evidence?.id" :key="evidence.id" :id="evidence.id" />
    </div>
  </div>
</template>

<style scoped>
.row{display:flex;gap:12px;align-items:flex-start}.row.user{flex-direction:row-reverse}
.avatar{width:28px;height:28px;border-radius:8px;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:650;background:var(--c-surface-2);color:var(--c-accent);border:1px solid var(--c-border)}.row.user .avatar{background:#183b4c;color:#9cddf5;border-color:#2b5869}.row.tool .avatar{color:var(--c-sub);background:transparent;border-style:dashed}.row.error .avatar{background:#3a1620;color:#ffb3c1;border-color:#7b2740}
.bubble{max-width:90%;min-width:0;background:var(--c-surface-2);border:1px solid var(--c-border);border-radius:var(--radius-md);padding:14px 18px}.row.assistant .bubble{flex:1;border-left:2px solid #3d819a}.row.user .bubble{background:#153242;border-color:#2a5264;max-width:78%}
.bubble-head{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-bottom:7px}.who{font-size:11px;font-weight:600;letter-spacing:.03em;color:var(--c-sub)}.text{font-size:14px;white-space:pre-wrap;word-break:break-word;line-height:1.8}
.row.tool .bubble{padding:4px 0;border:0;background:transparent}.row.tool .bubble-head{margin-bottom:2px}.row.tool .text{font-size:12px;color:var(--c-sub)}.row.error .bubble{border-color:#7b2740;color:#ffb3c1;background:#291b25}
.source-toggle{flex-shrink:0;background:transparent;color:var(--c-sub);border:1px solid var(--c-border);border-radius:5px;padding:4px 8px;font-size:11px}.source-toggle:hover{border-color:var(--c-accent);color:var(--c-ink)}
@media(max-width:600px){.row{gap:8px}.avatar{width:24px;height:24px}.bubble{max-width:calc(100% - 32px);padding:11px 12px}.row.user .bubble{max-width:84%}.row.assistant .bubble{max-width:calc(100% - 32px)}.text{font-size:13px}.bubble-head{gap:8px}}
</style>
