<script setup>
import {nextTick,onBeforeUnmount,ref,watch} from 'vue'
import {useRoute} from 'vue-router'
import {api} from '../api/client'
import {useSessionStore} from '../stores/session'
const props=defineProps({kinds:{type:Array,default:()=>['sample','time_range','video_frame','rag_excerpt']}})
const emit=defineEmits(['focus'])
const route=useRoute(),store=useSessionStore()
const card=ref(null),busy=ref(false),error=ref('')
let request=0,disposed=false
function currentContext(){
  const workspace=store.workspaceRevision,project=store.projectState.current?.id,revision=store.status.analysis_revision,results=store.status.results_revision
  return ()=>!disposed&&!store.switchingProject&&workspace===store.workspaceRevision&&project===store.projectState.current?.id&&revision===store.status.analysis_revision&&results===store.status.results_revision
}
async function load(){
  const ticket=++request,current=currentContext(),id=route.query.context_id
  card.value=null;error.value='';busy.value=false;emit('focus',null)
  if(!id)return
  if(typeof id!=='string'||id.length>128){error.value='引用标识无效，请重新选择来源。';return}
  if(store.switchingProject||!store.ready)return
  if(store.chatRunning||store.loading){error.value='当前任务执行中，结束后再核对引用来源。';return}
  busy.value=true
  try{
    const value=await api.questionContext(id)
    if(ticket!==request||!current())return
    if(!value||value.id!==id||!props.kinds.includes(value.kind)){error.value='该引用不属于本页面，请返回原来源。';return}
    card.value=value;emit('focus',value)
    await nextTick()
    if(ticket!==request||!current())return
    const anchor={sample:'trace-map',time_range:'time-series',video_frame:'video-quality',rag_excerpt:'rag-search',image_asset:'image-asset'}[value.kind]
    if(anchor)document.getElementById(anchor)?.scrollIntoView({block:'start'})
  }catch(e){if(ticket===request&&current())error.value=e.message||'无法核对引用，旧来源不会套用到当前数据。'}
  finally{if(ticket===request)busy.value=false}
}
watch(()=>[route.query.context_id,store.workspaceRevision,store.projectState.current?.id,store.status.analysis_revision,store.status.results_revision,store.switchingProject,store.ready],load,{immediate:true})
watch(()=>[store.chatRunning,store.loading],([running,loading])=>{
  // A source already verified for this scope stays visible during unrelated work.
  // Clearing it here would change the selector of the very request being prepared.
  if(!running&&!loading&&!card.value&&route.query.context_id)load()
})
onBeforeUnmount(()=>{disposed=true;request++})
</script>
<template>
  <section v-if="route.query.context_id" class="context-source" aria-label="追问来源核对">
    <p v-if="busy" role="status">正在核对追问来源与当前版本…</p>
    <p v-if="error" role="alert">{{error}}</p>
    <template v-if="card"><strong>{{card.title}}</strong><p>{{card.summary}}</p><p class="notice">{{card.notice}}</p><details v-if="card.excerpt"><summary>查看本次引用片段</summary><pre>{{card.excerpt}}</pre></details></template>
    <button v-if="error" type="button" :disabled="busy||store.loading||store.chatRunning||store.switchingProject" @click="load">重新核对来源</button>
  </section>
</template>
<style scoped>
.context-source{border:1px solid var(--c-border);border-left:3px solid var(--c-accent);background:var(--c-surface-2);padding:12px 14px;border-radius:8px;margin-bottom:16px;font-size: 15px;line-height:1.75;overflow-wrap:anywhere}.context-source p{margin:5px 0}.notice{color:var(--c-sub);font-size: 14px}.context-source pre{white-space:pre-wrap;max-height:300px;overflow:auto;font:inherit;color:var(--c-sub)}.context-source summary{cursor:pointer}.context-source button{font:inherit;font-size: 14px;color:var(--c-ink);background:var(--c-surface);border:1px solid var(--c-border);border-radius:6px;padding:6px 10px}
</style>
