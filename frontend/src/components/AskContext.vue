<script setup>
import {computed,onBeforeUnmount,ref,watch} from 'vue'
import {useRouter} from 'vue-router'
import {useSessionStore} from '../stores/session'
const props=defineProps({selector:{type:Object,default:null},disabled:Boolean,label:{type:String,default:'让AI分析此处'}})
const router=useRouter(),store=useSessionStore()
const busy=ref(false),error=ref('')
let ticket=0,disposed=false
const locked=computed(()=>props.disabled||busy.value||!props.selector||!store.ready||store.loading||store.chatRunning||store.switchingProject)
function context(){
  const workspace=store.workspaceRevision,project=store.projectState.current?.id,revision=store.status.analysis_revision,results=store.status.results_revision
  return ()=>!disposed&&!store.switchingProject&&workspace===store.workspaceRevision&&project===store.projectState.current?.id&&revision===store.status.analysis_revision&&results===store.status.results_revision
}
async function ask(){
  if(locked.value)return
  const request=++ticket,current=context(),signature=JSON.stringify(props.selector),selector=JSON.parse(signature)
  busy.value=true;error.value=''
  try{
    const card=await store.prepareQuestionContext(selector)
    if(!card||request!==ticket||!current())return
    if(signature!==JSON.stringify(props.selector)){
      store.clearQuestionContextById?.(card.id)
      error.value='当前选择已经变化，请重新选择后提问。';return
    }
    await router.push('/chat')
  }catch(e){if(request===ticket&&current())error.value=e.message}
  finally{if(request===ticket)busy.value=false}
}
watch(()=>[store.workspaceRevision,store.projectState.current?.id,store.status.analysis_revision,store.status.results_revision,store.switchingProject],()=>{ticket++;busy.value=false;error.value=''})
onBeforeUnmount(()=>{disposed=true;ticket++})
</script>
<template>
  <div class="ask-context"><button type="button" :disabled="locked" @click="ask">{{busy?'正在核对来源…':label}}</button><p v-if="error" role="alert">{{error}}</p></div>
</template>
<style scoped>
.ask-context{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:10px 0}.ask-context button{font:inherit;font-size: 14px;line-height:1.5;min-height:36px;padding:7px 12px;border:1px solid var(--c-accent);border-radius:var(--radius-sm,8px);color:var(--c-ink);background:var(--c-surface)}.ask-context button:disabled{opacity:.45;cursor:not-allowed}.ask-context p{font-size: 14px;color:#ffbc9d;margin:0;overflow-wrap:anywhere}
</style>
