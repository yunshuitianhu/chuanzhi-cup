<script setup>
import {computed} from 'vue'
import {RouterLink} from 'vue-router'
import {useSessionStore} from '../stores/session'
import {isQuestionContext,questionContextLocation,questionContextMatches} from '../utils/questionContext'
const props=defineProps({context:{type:Object,required:true},removable:Boolean,compact:Boolean})
defineEmits(['remove'])
const store=useSessionStore()
const valid=computed(()=>isQuestionContext(props.context))
const location=computed(()=>questionContextLocation(props.context))
const current=computed(()=>valid.value && questionContextMatches(props.context,store))
const names={text_quote:'文字引用',sample:'采样点',time_range:'时间范围',video_frame:'视频帧',rag_excerpt:'资料片段'}
</script>
<template>
  <section class="question-context" :class="{compact,historical:!current}" aria-label="问题上下文">
    <template v-if="valid">
      <div class="head"><span>{{names[context.kind]}} · {{context.title}}</span><button v-if="removable" type="button" aria-label="移除问题上下文" @click="$emit('remove')">移除</button></div>
      <p v-if="!compact">{{context.summary}}</p>
      <div class="meta"><span>{{current?(context.selection.source?.kind==='page'?'版本匹配 · 页面选文未经原文核验':'版本匹配，发送时仍会由服务端核对'):'历史来源，不能直接作为当前数据'}}</span><RouterLink v-if="location" :to="location">核对来源</RouterLink></div>
      <blockquote v-if="context.kind==='text_quote' && context.excerpt && !compact" class="selected-quote">{{context.excerpt}}</blockquote>
      <details v-if="context.kind!=='text_quote' && context.excerpt && !compact"><summary>所选原文片段</summary><pre>{{context.excerpt}}</pre></details>
      <details v-if="!compact && context.notice" class="context-notice"><summary>使用边界</summary><p class="notice">{{context.notice}}</p></details>
    </template>
    <p v-else>该上下文元数据无法核对，请重新选择来源。</p>
  </section>
</template>
<style scoped>
.question-context { border:1px solid var(--c-border); border-left:3px solid var(--c-accent); background:var(--c-bg); border-radius:var(--radius-sm); padding:13px 14px; margin:0 0 16px; font-size:12px; line-height:1.8; overflow-wrap:anywhere; min-width:0; }
.selected-quote { margin:12px 0 8px; padding:10px 12px; border-left:2px solid var(--c-border); background:var(--c-surface); white-space:pre-wrap; max-height:180px; overflow:auto; font-size:13px; line-height:1.85; }
.head { display:flex; align-items:flex-start; justify-content:space-between; gap:10px; font-weight:600; font-size:13px; color:var(--c-ink); }
.head button { border:0; background:transparent; color:var(--c-sub); padding:0 3px; min-height:24px; font:inherit; flex-shrink:0; }
p { margin:7px 0; white-space:pre-line; }
.meta { display:flex; flex-wrap:wrap; justify-content:space-between; align-items:baseline; gap:5px 10px; color:var(--c-sub); font-size:11px; }
.meta a { color:var(--c-accent); text-underline-offset:3px; white-space:nowrap; }
.notice { color:var(--c-sub); font-size:11px; }
.context-notice { margin-top:8px; }
.historical { border-left-color:var(--sev-mild); }
.compact { padding:8px 11px; margin-top:10px; margin-bottom:0; font-size:11px; }
.compact .head { font-size:12px; }
summary { cursor:pointer; color:var(--c-sub); font-size:11px; }
summary:hover { color:var(--c-ink); }
pre { white-space:pre-wrap; max-height:240px; overflow:auto; font:inherit; color:var(--c-sub); }
</style>
