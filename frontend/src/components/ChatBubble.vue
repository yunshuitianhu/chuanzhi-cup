<script setup>
import QuoteText from './QuoteText.vue'
import ReportEvidence from './ReportEvidence.vue'
import AnalysisCards from './AnalysisCards.vue'
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
      <AnalysisCards v-if="role==='assistant' && !incomplete && evidence?.id" :key="'cards-'+evidence.id" :bundle-id="evidence.id" />
      <ReportEvidence v-if="role==='assistant' && !incomplete && evidence?.id" :key="evidence.id" :id="evidence.id" />
    </div>
  </div>
</template>

<style scoped>

.row{display:flex;gap:14px;align-items:flex-start}.row.user{flex-direction:row-reverse}
.avatar{width:30px;height:30px;border-radius:9px;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:600;background:var(--c-accent-soft);color:var(--c-accent);border:1px solid #35576a}
.row.user .avatar{background:var(--c-surface-2);color:var(--c-sub);border-color:var(--c-border)}
.row.tool .avatar{color:var(--c-muted);background:transparent;border-style:dashed}
.row.error .avatar{background:#301f29;color:#ffbdc7;border-color:#794354}
.bubble{max-width:90%;min-width:0;border-radius:var(--radius-md);padding:14px 18px}
.row.assistant .bubble{flex:1;padding:2px 0 10px;max-width:calc(100% - 44px)}
.row.user .bubble{background:var(--c-surface-2);border:1px solid var(--c-border);max-width:78%}
.bubble-head{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-bottom:14px}
.who{font-size:12px;font-weight:600;letter-spacing:.03em;color:var(--c-sub)}
.text{font-size:14px;white-space:pre-wrap;word-break:break-word;line-height:1.9}
.row.tool .bubble{padding:4px 0;border:0;background:transparent}.row.tool .bubble-head{margin-bottom:2px}.row.tool .text{font-size:12px;color:var(--c-sub)}
.row.error .bubble{border:1px solid #794354;color:#ffbdc7;background:#301f29}
.source-toggle{flex-shrink:0;background:transparent;color:var(--c-sub);border:1px solid var(--c-border);border-radius:6px;padding:5px 9px;font-size:11px;min-height:30px}
.source-toggle:hover{border-color:var(--c-accent);color:var(--c-ink)}
@media(max-width:600px){.row{gap:9px}.avatar{width:25px;height:25px;font-size:10px}.bubble{max-width:calc(100% - 34px);padding:11px 12px}.row.user .bubble{max-width:84%}.row.assistant .bubble{max-width:calc(100% - 34px)}.text{font-size:13px}.bubble-head{gap:8px;margin-bottom:10px}}

</style>
