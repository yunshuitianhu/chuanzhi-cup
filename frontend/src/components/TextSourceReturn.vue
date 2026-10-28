<script setup>
import {nextTick,onBeforeUnmount,ref,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {useSessionStore} from '../stores/session'
import {api} from '../api/client'
import {questionContextMatches} from '../utils/questionContext'
import {textSourceNotice,withoutTextSource} from '../utils/textSourceNotice'
import QuestionContextCard from './QuestionContextCard.vue'

const store=useSessionStore(),route=useRoute(),router=useRouter()
const closing=ref(false)
let disposed=false,request=0
function clear(){store.textQuestions.sourceCard=null;store.textQuestions.sourceError=''}
async function source(){
  const ticket=++request,id=route.query.text_context,workspace=store.workspaceRevision
  clear()
  if(!id||!store.ready||store.switchingProject||closing.value)return
  if(typeof id!=='string'||!/^[a-f0-9]{32}$/.test(id)){
    store.textQuestions.sourceError=textSourceNotice({status:422});return
  }
  try{
    const card=await api.questionContext(id)
    if(disposed||ticket!==request||workspace!==store.workspaceRevision)return
    if(card.kind!=='text_quote'||!questionContextMatches(card,store)){
      store.textQuestions.sourceError=textSourceNotice({status:409});return
    }
    store.textQuestions.sourceCard=card
    await nextTick()
    if(ticket!==request||disposed)return
    const src=card.selection.source
    const element=document.getElementById(src.kind==='message'?'message-'+src.message_id:src.kind==='page'?src.anchor||'main-content':src.help_id||'text-source-return')
    element?.scrollIntoView({block:'center'});element?.focus({preventScroll:true})
  }catch(error){
    if(!disposed&&ticket===request&&workspace===store.workspaceRevision)
      store.textQuestions.sourceError=textSourceNotice(error)
  }
}
async function dismiss(){
  const workspace=store.workspaceRevision,id=route.query.text_context
  closing.value=true;request++;clear()
  try{
    const failure=await router.replace(withoutTextSource(route))
    if(failure)throw Error('Navigation did not complete')
  }
  catch{
    if(!disposed&&workspace===store.workspaceRevision&&route.query.text_context===id)
      store.textQuestions.sourceError={title:'暂时无法关闭提示',message:'页面导航未完成，请稍后再点击关闭。',retryable:false}
  }finally{closing.value=false}
}
watch([()=>route.query.text_context,()=>store.ready,()=>store.workspaceRevision,
  ()=>store.status.analysis_revision,()=>store.status.results_revision,()=>store.switchingProject],source,{immediate:true})
onBeforeUnmount(()=>{disposed=true;request++})
</script>
<template>
  <div v-if="store.textQuestions.sourceCard" id="text-source-return"><QuestionContextCard :context="store.textQuestions.sourceCard" /></div>
  <section v-if="store.textQuestions.sourceError" class="source-notice" role="status" aria-labelledby="source-notice-title">
    <div class="source-notice-copy">
      <strong id="source-notice-title">{{store.textQuestions.sourceError.title}}</strong>
      <p>{{store.textQuestions.sourceError.message}}</p>
      <p class="source-notice-note">你可以继续使用当前页面；此提示不会删除数据或已保存的对话。</p>
    </div>
    <div class="source-notice-actions">
      <button v-if="store.textQuestions.sourceError.retryable" type="button" @click="source">重试打开引用</button>
      <button type="button" :disabled="closing" @click="dismiss">关闭提示</button>
    </div>
  </section>
</template>
<style scoped>
.source-notice{display:flex;align-items:flex-start;justify-content:space-between;gap:18px;flex-wrap:wrap;padding:16px 18px;margin-bottom:20px;border:1px solid var(--c-border);border-left:3px solid var(--c-accent);border-radius:var(--radius-sm);background:var(--c-surface);color:var(--c-ink);font-size: 15px;line-height:1.8}
.source-notice-copy{flex:1 1 360px;min-width:0;overflow-wrap:anywhere}
.source-notice strong{font-size: 16px}
.source-notice p{margin:5px 0 0}
.source-notice .source-notice-note{color:var(--c-sub);font-size: 14px}
.source-notice-actions{display:flex;gap:8px;flex-wrap:wrap}
.source-notice button{font:inherit;min-height:36px;padding:6px 12px;border:1px solid var(--c-border);border-radius:var(--radius-sm);color:var(--c-accent);background:var(--c-bg);cursor:pointer}
.source-notice button:hover,.source-notice button:focus-visible{border-color:var(--c-accent)}
</style>
