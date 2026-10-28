<script setup>
import {computed,ref,watch,onMounted,onBeforeUnmount,nextTick} from 'vue'
import {useRouter} from 'vue-router'
import {useSessionStore} from '../stores/session'
import {api,sseHeaders,adoptSessionFrom} from '../api/client'
import {consumeAgentStream} from '../utils/agentStream'
import {quoteKey} from '../utils/textQuestions'
import SafeMarkdown from './SafeMarkdown.vue'
import TextQuoteCard from './TextQuoteCard.vue'
import QuestionContextCard from './QuestionContextCard.vue'
const store=useSessionStore(),router=useRouter(),panel=ref(null),scroller=ref(null)
const q=computed(()=>store.textQuestions)
const canAttachSide=computed(()=>store.ready&&!store.loading&&!store.chatRunning&&!store.switchingProject&&!sending.value)
const active=computed(()=>q.value.threads.find(t=>t.id===q.value.active))
const draftKey=computed(()=>q.value.active||(q.value.pendingSide?quoteKey(q.value.pendingSide):'new'))
const draft=computed({get:()=>q.value.drafts[draftKey.value]||'',set:value=>{q.value.drafts[draftKey.value]=value}})
const modal=ref(false),partial=ref(''),partialThreadId=ref(''),sending=ref(false),failedQuestion=ref(null),visionNote=ref('')
const sideFileInput=ref(null),sideUploading=ref(false),sideUploadError=ref(''),failedSideFile=ref(null)
const visiblePartial=computed(()=>{
  if(!q.value.streaming||q.value.active!==partialThreadId.value)return ''
  const recorded=active.value?.messages?.some(m=>m.role==='assistant'&&m.run_id&&m.run_id===q.value.run?.run_id)
  return recorded?'':partial.value
})
const visibleNotice=computed(()=>!q.value.notice?'':q.value.run?.thread_id===q.value.active?q.value.notice:'上一解释线程：'+q.value.notice)
let media,timer,previousFocus,overflow='',polling=false
function close(){q.value.open=false}
function keys(event){
  if(event.key==='Escape'){event.preventDefault();close();return}
  if(event.key!=='Tab'||!modal.value)return
  const controls=[...panel.value.querySelectorAll('button:not(:disabled),textarea:not(:disabled),select:not(:disabled),a[href],summary,[tabindex="0"]')].filter(e=>e.getClientRects().length)
  if((event.shiftKey&&document.activeElement===controls[0])||(!event.shiftKey&&document.activeElement===controls.at(-1))){event.preventDefault();(event.shiftKey?controls.at(-1):controls[0])?.focus()}
}
watch(()=>q.value.open,async open=>{
  if(open){previousFocus=document.activeElement;overflow=document.body.style.overflow;if(modal.value)document.body.style.overflow='hidden';await nextTick();panel.value?.querySelector('button')?.focus()}
  else{document.body.style.overflow=overflow;await nextTick();if(previousFocus?.isConnected)previousFocus.focus();else document.querySelector('.sidebar-toggle')?.focus()}
})
watch(()=>[store.ready,store.workspaceRevision],()=>{if(store.ready)store.loadSideThreads()},{immediate:true})
watch(()=>store.workspaceRevision,()=>{partial.value='';partialThreadId.value='';failedQuestion.value=null;visionNote.value='';sideUploadError.value='';failedSideFile.value=null})
watch(()=>q.value.active,()=>{visionNote.value='';sideUploadError.value='';failedSideFile.value=null})
onMounted(()=>{media=matchMedia('(max-width:1279px)');modal.value=media.matches;media.onchange=e=>{modal.value=e.matches;if(q.value.open)document.body.style.overflow=e.matches?'hidden':overflow};timer=setInterval(async()=>{if(polling||!store.ready||(!store.sideRunning&&!q.value.streaming))return;polling=true;try{await store.loadSideThreads()}finally{polling=false}},2000)})
onBeforeUnmount(()=>{clearInterval(timer);if(media)media.onchange=null;if(q.value.open)document.body.style.overflow=overflow})
async function transfer(){
  const quote=active.value?.quote
  if(!quote)return
  try{
    if(quote.kind==='text_quote'){
      store.captureTextQuote({text:quote.excerpt,source:quote.selection.source,title:quote.title},'main',quote.scope)
    }else{
      await store.transferQuoteToMain(quote)
    }
    q.value.open=false;await router.push('/chat')
  }catch(e){q.value.error=e.message}
}
function pickSideImage(){
  if(!canAttachSide.value){sideUploadError.value='当前任务尚未结束，结束后再添加图片。';return}
  sideFileInput.value?.click()
}
async function onSideFileChange(event){
  const file=event.target?.files?.[0]
  if(event.target)event.target.value=''
  if(file)await attachSideFile(file)
}
async function onSidePaste(event){
  const items=[...(event.clipboardData?.items||[])]
  const file=items.find(item=>item.kind==='file'&&String(item.type).startsWith('image/'))?.getAsFile()
  if(!file)return
  event.preventDefault()
  await attachSideFile(file)
}
async function attachSideFile(file){
  if(!file)return
  if(!String(file.type||'').startsWith('image/')&&!/\.(png|jpe?g|webp|bmp)$/i.test(file.name||'')){
    sideUploadError.value='请选择PNG或JPEG等常见图片。';failedSideFile.value=null;return
  }
  if(!canAttachSide.value){sideUploadError.value='当前任务尚未结束，结束后再添加图片。';return}
  sideUploading.value=true;sideUploadError.value='';failedSideFile.value=null;q.value.error=''
  try{
    const thread=await store.attachSideMedia(file)
    if(!thread)throw new Error('准备期间项目或版本已变化，请重新添加图片。')
    await nextTick()
    panel.value?.querySelector('textarea')?.focus()
  }catch(e){
    sideUploadError.value=e.message;failedSideFile.value=file
  }finally{sideUploading.value=false}
}
async function retrySideAttachment(){
  if(failedSideFile.value)await attachSideFile(failedSideFile.value)
}
async function stop(){try{await api.stopSideRun(q.value.run.run_id);await store.loadSideThreads()}catch(e){q.value.error=e.message}}
async function send(){
  const text=draft.value.trim()
  if(!text||sending.value||store.loading||store.chatRunning||!store.ready)return
  const workspace=store.workspaceRevision,project=store.projectState.current?.id
  const current=()=>workspace===store.workspaceRevision&&project===store.projectState.current?.id&&!store.switchingProject
  const originKey=draftKey.value
  let key=originKey,failed=false,title=active.value?.quote.title||q.value.pendingSide?.title||'所选引用'
  const showError=message=>{q.value.error=q.value.active===key?message:'另一解释线程（'+title+'）：'+message}
  sending.value=true;q.value.error='';q.value.notice='';partial.value='';visionNote.value=''
  try{
    const thread=await store.prepareSideThread()
    if(!current())return
    if(!thread){q.value.error='准备期间选择已变化，本轮问题未发送；原草稿仍保留。';return}
    key=thread.id
    partialThreadId.value=key
    q.value.run={run_id:null,thread_id:key,status:'running',busy:true}
    q.value.streaming=true;store.sideRunning=true;store.chatRunning=true
    const endpoint='/api/side/threads/'+encodeURIComponent(thread.id)+'/chat'
    const res=await fetch(endpoint,{method:'POST',headers:sseHeaders(),body:JSON.stringify({message:text,request_id:crypto.randomUUID()})})
    if(!res.ok||!res.body){const body=await res.json().catch(()=>({}));throw Error(body.detail||'侧边问答发送失败')}
    if(!current())return
    adoptSessionFrom(res)
    const runId=res.headers.get('X-Agent-Run-Id')
    q.value.run={run_id:runId,thread_id:key,status:'running',busy:true}
    // Clear only the text actually submitted. Users can keep editing during preparation.
    for(const draftId of new Set([originKey,key]))if(q.value.drafts[draftId]?.trim()===text)q.value.drafts[draftId]=''
    await consumeAgentStream(res.body,(event,payload)=>{
      if(!current())return
      if(payload.thread_id!==thread.id)throw Error('收到其他线程的事件，已停止显示')
      if(runId&&payload.run_id&&payload.run_id!==runId)throw Error('收到其他任务的事件，已停止显示')
      if(payload.progress)q.value.run={...payload.progress,thread_id:thread.id}
      if(event==='vision'){
        const images=Array.isArray(payload.images)?payload.images:[]
        visionNote.value=payload.status==='sent'
          ? `已向模型发送${images.length>1?` ${images.length} 张`:' 1 张'}图片`
          : `图片未读取成功：${(Array.isArray(payload.errors)?payload.errors:[payload.error]).filter(Boolean).join('；')||'未知原因'}`
      }
      if(event==='delta')partial.value+=payload.text||''
      if(event==='error'){failed=true;showError(payload.message||'本轮解释未完成')}
      if(event==='done'&&payload.status!=='succeeded'){failed=true;showError('本轮解释已停止；可保留问题后显式重试。')}
    })
  }catch(e){failed=true;if(current())showError(e.message)}
  finally{
    if(current()){
      if(failed){
        if(!q.value.drafts[key])q.value.drafts[key]=text
        failedQuestion.value={text,title}
      }
      q.value.streaming=false
      await store.loadSideThreads()
      if(!store.sideRunning)partial.value=''
    }
    sending.value=false
  }
}
function composerKey(e){if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing&&e.keyCode!==229){e.preventDefault();send()}}
</script>
<template>
  <button class="side-launch" type="button" :aria-expanded="q.open" @click="q.open=!q.open">侧边问答<span v-if="store.sideRunning"> · 进行中</span></button>
  <Teleport to="body">
    <button v-if="q.open&&modal" class="side-shade" aria-label="关闭侧边问答" tabindex="-1" @click="close"></button>
    <aside v-show="q.open" ref="panel" class="side-questions" :role="modal?'dialog':'complementary'" :aria-modal="modal?'true':undefined" aria-label="文字解释侧边问答" @keydown="keys">
      <header><div><h2>围绕文字或图片提问</h2><p>独立解释线程 · 不改写主报告</p></div><button type="button" @click="close">收起</button></header>
      <label class="threads">历史解释线程<select v-model="q.active" :disabled="sending"><option value="">当前新引用</option><option v-for="t in q.threads" :key="t.id" :value="t.id">{{t.quote.title}} · {{t.messages.length}}条{{t.historical?' · 历史版本':''}}</option></select></label>
      <div ref="scroller" class="side-history" tabindex="0" aria-label="侧边解释消息">
        <QuestionContextCard v-if="active" :context="active.quote" />
        <TextQuoteCard v-else-if="q.pendingSide" :quote="q.pendingSide" removable @remove="q.pendingSide=null" />
        <p v-else class="tip">从报告、资料原文或页面说明选择文字，或点击“引用此段”；也可以直接粘贴一张截图。</p>
        <p v-if="active?.historical" class="tip">该引用属于历史版本，仅供阅读；重新选择当前来源后才能继续提问。</p>
        <article v-for="(m,i) in active?.messages||[]" :key="active.id+':'+i" :class="['side-message',m.role]">
          <strong>{{m.role==='user'?'我':m.role==='assistant'?'解释助手':'未完成提示'}}</strong><span v-if="m.incomplete"> · 未完整完成</span><span v-if="m.vision"> · {{m.vision.sent?'已发送图片':'图片未读取'}}</span>
          <SafeMarkdown v-if="m.role==='assistant'" :content="m.content" /><p v-else>{{m.content}}</p>
        </article>
        <article v-if="visiblePartial" class="side-message assistant"><strong>正在解释…</strong><SafeMarkdown :content="visiblePartial" /></article>
      </div>
      <p v-if="q.error" class="failure" role="alert">{{q.error}}</p><p v-if="visionNote" class="tip" role="status">{{visionNote}}</p><p v-if="visibleNotice" class="tip" role="status">{{visibleNotice}}</p>
      <details v-if="failedQuestion" class="tip"><summary>查看未完成问题（{{failedQuestion.title}}）</summary><p style="white-space:pre-wrap;max-height:120px;overflow:auto">{{failedQuestion.text}}</p></details>
      <p v-if="sideUploadError" class="failure" role="alert">{{sideUploadError}}</p>
      <p v-if="store.chatRunning&&!q.streaming&&!store.sideRunning" class="tip">主任务执行中，可以整理引用和问题；结束后再发送。</p>
      <div class="side-actions"><button v-if="active" type="button" :disabled="sending" @click="transfer">将引用添加到主对话</button><button v-if="store.sideRunning" type="button" :disabled="!q.run?.run_id||!q.run?.busy" @click="stop">停止解释</button><button type="button" @click="store.loadSideThreads()">核对状态</button></div>
      <form @submit.prevent="send"><textarea v-model="draft" aria-label="围绕引用文字或图片提问" rows="3" maxlength="10000" placeholder="这句话或这张图具体是什么意思？依据或适用条件是什么？" :disabled="!store.ready||store.switchingProject" @keydown="composerKey" @paste="onSidePaste"></textarea>
        <div class="send-row">
          <div class="side-tools">
            <button type="button" class="side-attach" :disabled="!canAttachSide" :title="canAttachSide?'添加或粘贴一张截图':'当前任务结束后可添加图片'" @click="pickSideImage">图片</button>
            <input ref="sideFileInput" type="file" accept="image/png,image/jpeg,image/webp,image/bmp" hidden @change="onSideFileChange">
            <button v-if="sideUploading" type="button" class="side-attach" disabled>正在读取图片…</button>
            <button v-else-if="failedSideFile" type="button" class="side-attach" @click="retrySideAttachment">重试读取图片</button>
            <span>Enter发送 · Shift+Enter换行</span>
          </div>
          <button type="submit" :disabled="!draft.trim()||sending||store.chatRunning||store.loading||(!active&&!q.pendingSide)||active?.historical">{{sending?'解释中…':'发送问题'}}</button>
        </div>
      </form>
    </aside>
  </Teleport>
</template>
<style scoped>
.side-launch { font:inherit; font-size:12px; background:var(--c-surface-2); border:1px solid var(--c-border); border-radius:var(--radius-sm); color:var(--c-ink); padding:8px 12px; min-height:36px; white-space:nowrap; }
.side-launch:hover { border-color:var(--c-accent); }
.side-launch[aria-expanded=true] { border-color:var(--c-accent); color:var(--c-accent); }
.side-shade { position:fixed; inset:0; background:#07101899; border:0; z-index:1090; }
.side-questions { position:fixed; inset:0 0 0 auto; width:400px; max-width:100vw; height:100dvh; z-index:1100; background:var(--c-surface); border-left:1px solid var(--c-border); display:flex; flex-direction:column; gap:12px; padding:20px; box-shadow:-12px 0 40px #0003; color:var(--c-ink); min-width:0; }
header { display:flex; justify-content:space-between; align-items:flex-start; gap:12px; padding-bottom:14px; border-bottom:1px solid var(--c-border); }
h2 { font-size:18px; line-height:1.55; font-weight:650; margin:0; }
header p,.tip { font-size:12px; color:var(--c-sub); margin:5px 0; line-height:1.8; overflow-wrap:anywhere; }
button,textarea,select { font:inherit; font-size:12px; color:var(--c-ink); background:var(--c-surface-2); border:1px solid var(--c-border); border-radius:var(--radius-sm); padding:8px 10px; }
button { min-height:36px; transition:border-color var(--duration-fast),background var(--duration-fast); }
button:hover:not(:disabled) { border-color:var(--c-accent); }
button:disabled { opacity:.45; }
header > button { flex-shrink:0; }
.threads { font-size:12px; color:var(--c-sub); display:flex; flex-direction:column; gap:7px; }
select { width:100%; min-height:39px; max-width:100%; background:var(--c-bg); }
.side-history { flex:1; min-height:100px; overflow:auto; overscroll-behavior:contain; padding:2px 3px 8px 0; overflow-wrap:anywhere; }
.side-message { margin:20px 0; font-size:13px; line-height:1.8; }
.side-message.user { padding:11px 13px; background:var(--c-surface-2); border-radius:var(--radius-sm); }
.side-message p { white-space:pre-wrap; margin:5px 0 0; }
.side-message strong { font-size:12px; font-weight:500; color:var(--c-sub); }
.side-message :deep(.report-markdown) { font-size:13px; line-height:1.85; margin-top:7px; }
.failure { font-size:12px; color:#ffb7a7; margin:0; line-height:1.7; overflow-wrap:anywhere; }
.side-actions { display:flex; gap:7px; flex-wrap:wrap; }
.side-actions button { font-size:12px; min-height:32px; }
.send-row { display:flex; justify-content:space-between; align-items:center; gap:8px; margin-top:9px; flex-wrap:wrap; }
.side-tools { display:flex; align-items:center; gap:6px; flex-wrap:wrap; min-width:0; }
.side-attach { font-size:12px; min-height:30px; padding:4px 9px; background:transparent; color:var(--c-sub); }
.side-attach:hover:not(:disabled) { border-color:var(--c-accent); color:var(--c-ink); }
.send-row span { font-size:12px; color:var(--c-sub); }
.send-row button { background:var(--c-accent); border-color:var(--c-accent); color:#081823; font-weight:600; padding-inline:16px; }
textarea { width:100%; resize:vertical; min-height:88px; max-height:180px; font-size:13px; line-height:1.7; background:var(--c-bg); }
textarea::placeholder { color:var(--c-sub); opacity:.85; }
form { border-top:1px solid var(--c-border); padding-top:14px; }
details.tip summary { cursor:pointer; }
@media(max-width:1279px) { .side-questions { width:min(440px,100vw); padding:18px; } }
@media(max-width:479px) { .side-questions { padding:14px; gap:10px; } header { padding-bottom:10px; } h2 { font-size:17px; } .side-history { min-height:70px; } textarea { min-height:76px; } }
</style>
