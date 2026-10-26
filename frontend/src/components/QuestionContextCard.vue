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
      <div class="meta"><span>{{current?'版本匹配，发送时仍会由服务端核对':'历史来源，不能直接作为当前数据'}}</span><RouterLink v-if="location" :to="location">核对来源</RouterLink></div>
      <p v-if="!compact" class="notice">{{context.notice}}</p>
      <blockquote v-if="context.kind==='text_quote' && context.excerpt && !compact" class="selected-quote">{{context.excerpt}}</blockquote>
      <details v-if="context.kind!=='text_quote' && context.excerpt && !compact"><summary>所选原文片段</summary><pre>{{context.excerpt}}</pre></details>
    </template>
    <p v-else>该上下文元数据无法核对，请重新选择来源。</p>
  </section>
</template>
<style scoped>
.question-context{border:1px solid var(--c-border);border-left:3px solid var(--c-accent);background:var(--c-surface-2);border-radius:8px;padding:11px 13px;margin:0 0 14px;font-size:12px;line-height:1.7;overflow-wrap:anywhere}
.selected-quote{margin:8px 0;padding:7px 10px;border-left:2px solid var(--c-accent);background:var(--c-bg);white-space:pre-wrap;max-height:140px;overflow:auto}
.head{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;font-weight:600;color:var(--c-ink)}.head button{border:0;background:transparent;color:var(--c-sub);padding:0 3px;font:inherit;flex-shrink:0}
p{margin:6px 0;white-space:pre-line}.meta{display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px;color:var(--c-sub);font-size:11px}.meta a{color:var(--c-accent)}.notice{color:var(--c-sub);font-size:11px}.historical{border-left-color:var(--sev-mild)}.compact{padding:7px 10px;margin-top:10px;margin-bottom:0;font-size:11px}summary{cursor:pointer}pre{white-space:pre-wrap;max-height:240px;overflow:auto;font:inherit;color:var(--c-sub)}
</style>
