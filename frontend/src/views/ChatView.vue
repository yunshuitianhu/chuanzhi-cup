<script setup>
/**
 * 智能对话页 —— 对应 app.py 的 tab1。
 *
 * 「开始自动分析」会让后端 Agent 自主编排整条工具链（12+6 步 SOP），
 * 与自由对话共用同一套 Function Calling 循环。
 */
import { nextTick, ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useSessionStore } from '../stores/session'
import { sseHeaders, adoptSessionFrom } from '../api/client'
import { toastErr } from '../composables/useToast'
import Panel from '../components/Panel.vue'
import ChatBubble from '../components/ChatBubble.vue'
import TextQuoteCard from '../components/TextQuoteCard.vue'
import QuestionContextCard from '../components/QuestionContextCard.vue'
import ContextSourceFocus from '../components/ContextSourceFocus.vue'
import {isQuestionContext,questionContextMatches} from '../utils/questionContext'
import AgentProgress from '../components/AgentProgress.vue'
import InvestigationBoard from '../components/InvestigationBoard.vue'
import { consumeAgentStream } from '../utils/agentStream'
import { createChatScroll } from '../utils/chatScroll'

const store = useSessionStore()
const input = computed({get:()=>store.chatDraft||'',set:value=>{store.chatDraft=value}})
const canAsk = computed(()=>store.ready && !store.loading && !store.switchingProject)
const { chatRunning: running, chatMessages: messages } = storeToRefs(store)
const analysisExpanded = ref(false)
const visionStatus = ref('')
const aiNotice = ref('')
const attachmentBusy = ref(false)
const attachmentError = ref('')
const failedAttachment = ref(null)
const fileInput = ref(null)
const composerEl = ref(null)
const canAttach = computed(()=>!running.value && !store.loading && store.ready && !store.switchingProject)
const selectedKind = computed(()=>store.questionContext?.kind || (store.textQuestions?.pendingMain ? 'text_quote' : ''))
const aiConfigured = computed(()=>!store.config?.keys || store.config.keys.deepseek!==false)
const workbench = computed(()=>{
  const status=store.status||{}
  const hasData=!!status.has_data
  const context=store.questionContext
  const pending=store.textQuestions?.pendingMain
  const draft=(store.chatDraft||'').trim()
  const run=store.agentRun
  const screened=!!(status.analysis_done || status.has_detection)
  const dataText=hasData ? `${status.rows??0} 条记录${status.data_identity?.kind==='synthetic'?' · 仿真':''}` : '未载入数据'
  const questionText=context?.title ? `已选：${context.title}` : pending?.title ? `选文：${pending.title}` : draft ? `草稿：${draft.slice(0,32)}` : '未提出'
  let progressText='尚未分析'
  if(run?.busy) progressText=run.stage||'分析进行中'
  else if(run?.status==='failed') progressText='上轮失败'
  else if(run?.status==='partial') progressText='上轮部分完成'
  else if(run?.status==='cancelled') progressText='上轮已停止'
  else if(run?.status==='succeeded') progressText='本轮已完成'
  else if(status.analysis_done) progressText='已有分析结果'
  else if(status.has_detection) progressText='已完成基础筛查'
  const evidenceText=!hasData ? '等待数据' : screened ? `${status.black_spot_count??0} 个预警点` : '尚未筛查'
  const nextText=!hasData ? '先载入示例' : screened ? '核对证据并安排复测' : '开始分析'
  return {hasData,screened,dataText,questionText,progressText,evidenceText,nextText}
})
function focusComposer(){ nextTick(()=>composerEl.value?.focus()) }
function scrollToProgress(){ document.getElementById('agent-progress')?.scrollIntoView({block:'nearest'}) }
async function openInvestigationBoard(){
  investigationVisited.value=true
  await nextTick()
  if(investigationDetails.value){ investigationDetails.value.open=true; investigationDetails.value.scrollIntoView({block:'start'}) }
}
function ensureAi(){
  if(aiConfigured.value){aiNotice.value='';return true}
  aiNotice.value='未配置 DeepSeek Key：可先载入内置示例、查看图表和运行基础筛查；AI 问答与报告需在服务端配置 Key 并重启后端后重试。'
  return false
}
async function recheckConfig(){
  try{await store.refreshConfig();if(aiConfigured.value)aiNotice.value=''}
  catch(e){toastErr(`配置检查失败：${e.message}`)}
}
const contextLabels={image_asset:'图片',video_frame:'视频帧',text_quote:'文字',sample:'采样点',time_range:'时间范围',rag_excerpt:'资料片段'}
const collapsedContext = computed(()=>{
  if(store.questionContext)return {kind:store.questionContext.kind,title:store.questionContext.title,label:contextLabels[store.questionContext.kind]||'来源'}
  const pending=store.textQuestions?.pendingMain
  return pending?{kind:'text_quote',title:pending.title||'所选文字',label:'文字'}:null
})
function removeCollapsed(){
  if(store.questionContext){store.clearQuestionContext();return}
  if(store.textQuestions?.pendingMain){store.textQuestions.pendingMain=null;store.questionContextNotice=''}
}
const scroller = ref(null)
const clock=ref(Date.now())
const lastRequest=ref(null)
const scrollState = ref({following:true,hasUnseen:false})
const investigationVisited = ref(false)
const investigationDetails = ref(null)
const scrollController = createChatScroll({
  getElement: () => scroller.value,
  hasSelection: () => hasStreamSelection(),
  onChange: state => { scrollState.value = state },
})
function hasStreamSelection() {
  const selection = window.getSelection()
  return !!selection && !selection.isCollapsed && !!scroller.value &&
    (scroller.value.contains(selection.anchorNode) || scroller.value.contains(selection.focusNode))
}
function selectionChanged() { if (hasStreamSelection()) scrollController.pause() }
function readIntent(event) {
  if (event.type === 'wheel' && event.deltaY >= 0) return
  if (event.type === 'keydown' && !['ArrowUp','PageUp','Home'].includes(event.key)) return
  scrollController.pause()
}
async function latest() {
  if (hasStreamSelection()) window.getSelection()?.removeAllRanges()
  await nextTick()
  scrollController.jumpToLatest()
}
function composerKey(event) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
    event.preventDefault()
    send()
  }
}
function openInvestigation(event) { if (event.target.open) investigationVisited.value = true }
watch(() => [store.selectedInvestigationId,store.investigationRevision], async ([id]) => {
  if (!id || store.switchingProject) return
  const workspace=store.workspaceRevision
  const current=()=>workspace===store.workspaceRevision && !store.switchingProject && id===store.selectedInvestigationId
  investigationVisited.value = true
  await nextTick()
  if (!current()) return
  if (investigationDetails.value) investigationDetails.value.open = true
  await nextTick()
  if (current()) document.getElementById('investigation-board')?.scrollIntoView({block:'start'})
})
const suggestedQuestions = [
  '这批监测数据有哪些缺测或异常需要复核？',
  '请解释黑臭筛查的判定依据和适用限制。',
  '请根据现有证据建议下一步复测指标。',
]
let timer, polling=false
const elapsed=computed(()=>Math.max(0,(store.agentRun?.elapsed_s||0)+
  (store.agentRun?.busy ? (clock.value-store.agentRunAt)/1000 : 0)))

async function refreshRun() {
  if(polling)return
  polling=true
  try { await store.refreshAgentRun() } catch { /* 进度面板显示状态未知，不能当作完成 */ }
  finally { polling=false }
}
async function stop() {
  try { await store.stopAgentRun() } catch(e) { toastErr(`停止请求未确认：${e.message}`); await refreshRun() }
}
onMounted(()=>{
  document.addEventListener('selectionchange',selectionChanged)
  scrollDown()
  refreshRun()
  let ticks=0
  timer=setInterval(()=>{clock.value=Date.now();if(++ticks%2===0 && store.agentRun?.busy)refreshRun()},1000)
})
onUnmounted(()=>{
  clearInterval(timer)
  scrollController.dispose()
  document.removeEventListener('selectionchange',selectionChanged)
})

async function scrollDown() {
  await nextTick()
  scrollController.schedule()
}

async function toggleAnalysis(){
  const top=scroller.value?.scrollTop||0
  analysisExpanded.value=!analysisExpanded.value
  await nextTick()
  if(scroller.value)scroller.value.scrollTop=top
}

async function autoAnalyze() {
  if(!ensureAi())return
  return submit('请开始自动分析。',true)
}

async function send() {
  const text = input.value.trim()
  if (!text || running.value || !store.ready || store.loading || !canAsk.value) return
  if(!ensureAi())return
  if(store.textQuestions?.pendingMain){
    try{if(!await store.preparePendingQuote())return}catch(e){toastErr(e.message);return}
  }
  input.value = ''
  visionStatus.value = ['image_asset','video_frame'].includes(selectedKind.value) ? '准备图片' : ''
  return submit(text,false)
}

function autosize(){
  const el=composerEl.value
  if(!el)return
  el.style.height='auto'
  el.style.height=Math.min(140,Math.max(52,el.scrollHeight))+'px'
}
function pickImage(){
  if(!canAttach.value){attachmentError.value='当前任务尚未结束，结束后再添加图片。';return}
  fileInput.value?.click()
}
async function onFileChange(event){
  const file=event.target?.files?.[0]
  if(event.target)event.target.value=''
  if(file)await attachFile(file)
}
async function onPaste(event){
  const items=[...(event.clipboardData?.items||[])]
  const file=items.find(item=>item.kind==='file'&&String(item.type).startsWith('image/'))?.getAsFile()
  if(!file)return
  event.preventDefault()
  await attachFile(file)
}
async function attachFile(file){
  if(!file)return
  if(!String(file.type||'').startsWith('image/')&&!/\.(png|jpe?g|webp|bmp)$/i.test(file.name||'')){
    attachmentError.value='请选择PNG或JPEG等常见图片。';failedAttachment.value=null;return
  }
  if(!canAttach.value){attachmentError.value='当前任务尚未结束，结束后再添加图片。';return}
  attachmentBusy.value=true;attachmentError.value='';failedAttachment.value=null
  visionStatus.value=''
  try{
    await store.attachMedia(file)
    await scrollDown()
  }catch(e){
    attachmentError.value=e.message;failedAttachment.value=file
  }finally{attachmentBusy.value=false}
}
async function retryAttachment(){
  if(failedAttachment.value)await attachFile(failedAttachment.value)
}
async function openSide(){
  if(!store.questionContext)return
  try{await store.openSideThreadForContext(store.questionContext)}
  catch(e){toastErr(e.message)}
}
watch(()=>store.workspaceRevision,()=>{
  analysisExpanded.value=false
  visionStatus.value=''
  attachmentError.value=''
  failedAttachment.value=null
})
watch(input,()=>nextTick(autosize))
watch(()=>store.questionContext?.id,id=>{if(!id&&!running.value)visionStatus.value=''})

async function retryLast() {
  if(lastRequest.value?.context_id && lastRequest.value.context_id!==store.questionContext?.id) {
    if(!input.value.trim())input.value=lastRequest.value.text
    store.questionContextNotice='上次问题的上下文已移除或变化，请核对选择后重新发送。'
    return
  }
  if(lastRequest.value)return submit(lastRequest.value.text,lastRequest.value.auto)
}
async function submit(text,auto) {
  if(running.value || !store.ready || store.loading || (!canAsk.value && !auto))return
  if(!ensureAi())return
  const workspace=store.workspaceRevision, project=store.projectState.current?.id
  const current=()=>workspace===store.workspaceRevision && project===store.projectState.current?.id
  const token=crypto.randomUUID()
  const questionContext=auto?null:store.questionContext
  if(questionContext && (!isQuestionContext(questionContext)||!questionContextMatches(questionContext,store))) {
    store.clearQuestionContext('所选上下文已过期，请重新选择后发送。')
    if(!input.value.trim())input.value=text
    return
  }
  lastRequest.value={text,auto,context_id:questionContext?.id??null}
  if(!store.beginAgentStream())return
  messages.value.push({role:'user',content:text,uiRequest:token,...(questionContext?{question_context:questionContext}:{})})
  latest()
  let runId=null, answer=null
  function incomplete() {
    for(const message of messages.value)if(message.uiRequest===token && message.role==='assistant')message.incomplete=true
  }
  try {
    const res = await fetch('/api/agent/chat', {
      method: 'POST',
      headers: sseHeaders(),
      body: JSON.stringify({ message:text, auto, ...(questionContext?{context_id:questionContext.id}:{}) }),
    })
    if (!res.ok || !res.body) {
      const error=await res.json().catch(()=>({}))
      if(questionContext && [404,409,422].includes(res.status) && current()) {
        if(res.status===409&&res.headers?.get('X-Session-Conflict')==='busy')
          store.questionContextNotice='当前任务忙碌，引用和原问题已保留；任务结束后可重新发送。'
        else store.clearQuestionContext('所选来源已失效，请重新选择；原问题已保留。')
        if(!input.value.trim())input.value=text
      }
      throw new Error(error.detail || `HTTP ${res.status}`)
    }
    adoptSessionFrom(res)
    runId=res.headers.get('X-Agent-Run-Id')
    if(current())store.agentActiveId=runId
    await consumeAgentStream(res.body,async(event,payload)=>{
      if(!current())return
      if(runId && payload.run_id && runId!==payload.run_id)throw new Error('收到其它任务的事件，请核对当前任务')
      runId ||= payload.run_id
      store.agentActiveId=runId
      if(payload.progress)store.adoptAgentRun(payload.progress)
      if(event==='tool') {
        messages.value.push({role:'tool',content:`开始：${payload.name}`,uiRequest:token})
        answer=null
      } else if(event==='tool_done') {
        messages.value.push({role:payload.ok?'tool':'error',uiRequest:token,
          content:`${payload.name}：${payload.status==='skipped'?'已跳过':payload.ok?'完成':'失败，等待后续处理'}`})
        answer=null
      } else if(event==='vision') {
        const images=Array.isArray(payload.images)?payload.images:[]
        if(payload.status==='sent'){
          visionStatus.value=images.length>1?`已发送 ${images.length} 张图片`:'已发送图片'
          messages.value.push({role:'tool',uiRequest:token,
            content:`图片已发送给模型查看：${images.map(image=>image.label||'图片').join('、')||'本轮图片'}`})
        }else{
          visionStatus.value='图片未读取成功'
          const reason=(Array.isArray(payload.errors)?payload.errors:[payload.error]).filter(Boolean).join('；')
          messages.value.push({role:'error',uiRequest:token,
            content:`图片没有读取成功：${reason||'未知原因'}。可以重试读取图片。`})
        }
        answer=null
      } else if(event==='delta') {
        if(!answer) {
          messages.value.push({role:'assistant',content:'',run_id:runId,uiRequest:token,...(questionContext?{question_context:questionContext}:{})})
          answer=messages.value[messages.value.length-1]
        }
        answer.content+=payload.text || ''
      } else if(event==='done' && payload.status!=='succeeded') {
        incomplete()
        messages.value.push({role:'error',uiRequest:token,content:payload.status==='cancelled'
          ? '本轮已停止。已完成的部分分析可保留；重新分析会从当前数据重新取证。'
          : '本轮未完整完成，请查看失败或缺失步骤后重新分析。'})
      } else if(event==='error') {
        if(visionStatus.value==='准备图片')visionStatus.value='图片发送状态未知'
        incomplete()
        messages.value.push({role:'error',content:payload.message || '本轮执行失败',uiRequest:token})
      }
      scrollDown()
    })
  } catch (e) {
    if(current()) {
      incomplete()
      messages.value.push({role:'error',content:`${e.message}；请核对任务状态后重试。`,uiRequest:token})
      // 请求失败时保留原问题，避免用户重新输入；自动分析不把内部提示写回输入框。
      if(!auto && !input.value.trim())input.value=text
      toastErr(e.message)
    }
  } finally {
    if(current()) {
      store.chatStreaming=false
      store.agentActiveId=null
      running.value=true // 收到服务端状态前，不将连接关闭解释为可重新执行
      await refreshRun()
      await store.refreshStatus().catch(()=>{store.agentConnectionError='连接中断，尚未核实任务与数据状态。'})
    }
  }
}

</script>

<template>
  <Panel class="chat-workspace" title="智能对话" dense>
    <template #actions>
      <button class="primary" :disabled="!store.canAnalyze || !store.ready || store.loading || running" :aria-busy="running" @click="autoAnalyze">
        <svg aria-hidden="true" viewBox="0 0 20 20"><path d="m10 2 1.8 5.7L18 10l-6.2 2.3L10 18l-1.8-5.7L2 10l6.2-2.3Z" /></svg>
        {{ store.sideRunning ? '侧边问答进行中…' : running ? '分析中…' : '开始自动分析' }}
      </button>
    </template>
    <ContextSourceFocus :kinds="['image_asset']" />
    <div id="image-asset" class="chat-summary" aria-label="当前对话摘要">
      <span class="project-tag">{{ store.projectState.current?.name || '临时巡航会话' }}</span>
      <span class="summary-meta">{{ store.status.has_data ? store.status.rows + ' 条记录' : '等待数据' }} · 目标 {{ store.config.target_water_class }} 类
        <span v-if="store.status.data_identity?.kind==='synthetic'" class="data-kind">仿真</span></span>
      <span v-if="visionStatus" class="vision-chip" role="status">{{ visionStatus }}</span>
      <span v-if="store.config.keys && !store.config.keys.deepseek" class="vision-chip" title="未配置 DeepSeek Key：仍可载入示例、查看图表和基础筛查；AI 问答与报告不可用">AI 未配置</span>
      <button v-if="store.questionContext && ['video_frame','image_asset','text_quote'].includes(store.questionContext.kind)" type="button" class="info-toggle side-ask" :disabled="running || store.loading" @click="openSide">侧边追问</button>
      <button type="button" class="info-toggle" :aria-expanded="analysisExpanded" aria-controls="chat-analysis-info" @click="toggleAnalysis">{{ analysisExpanded ? '收起分析信息' : '展开分析信息' }}</button>
    </div>
    <section class="workbench" aria-label="研判工作台状态">
      <div class="wb-cell">
        <span class="wb-label">数据</span>
        <strong class="wb-value">{{workbench.dataText}}</strong>
        <a class="wb-action" href="#data-samples">数据与示例</a>
      </div>
      <div class="wb-cell">
        <span class="wb-label">当前问题</span>
        <strong class="wb-value" :title="workbench.questionText">{{workbench.questionText}}</strong>
        <button type="button" class="wb-action" @click="focusComposer">{{store.questionContext ? '换个问题' : '输入问题'}}</button>
      </div>
      <div class="wb-cell">
        <span class="wb-label">分析进度</span>
        <strong class="wb-value" :title="workbench.progressText">{{workbench.progressText}}</strong>
        <button v-if="running" type="button" class="wb-action" @click="scrollToProgress">查看进度</button>
        <button v-else-if="workbench.hasData && !workbench.screened" type="button" class="wb-action"
          :disabled="!store.ready || store.loading" @click="autoAnalyze">开始自动分析</button>
        <a v-else-if="workbench.hasData" class="wb-action" href="/charts">查看图表</a>
        <span v-else class="wb-wait">等待数据</span>
      </div>
      <div class="wb-cell">
        <span class="wb-label">证据 · 下一步</span>
        <strong class="wb-value" :title="workbench.evidenceText">{{workbench.evidenceText}}</strong>
        <a v-if="!workbench.hasData" class="wb-action" href="#data-samples">载入示例</a>
        <button v-else type="button" class="wb-action" @click="openInvestigationBoard">{{workbench.nextText}}</button>
      </div>
    </section>
    <div v-if="!analysisExpanded && collapsedContext" class="context-line" aria-label="当前选择">
      <span class="context-kind">{{ collapsedContext.label }}</span>
      <span class="context-name" :title="collapsedContext.title">{{ collapsedContext.title }}</span>
      <button type="button" class="context-remove" aria-label="移除当前选择" @click="removeCollapsed">移除</button>
    </div>
    <p v-if="store.questionContextNotice" class="tip warning" role="status">{{store.questionContextNotice}}</p>
    <div id="chat-analysis-info"><div v-if="analysisExpanded" class="analysis-info">
      <div class="context-strip" aria-label="详细分析上下文">
        <span class="project-tag">{{ store.projectState.current?.name || '临时巡航会话' }}</span>
        <span>{{ store.status.has_data ? store.status.rows + ' 条记录' : '未载入监测表' }}<template v-if="store.status.data_filename"> · {{ store.status.data_filename }}</template></span>
        <span v-if="store.status.video_frames">{{ store.status.video_frames }} 帧<template v-if="store.status.video_filename"> · {{ store.status.video_filename }}</template></span>
        <span v-else>未载入视频</span>
        <span>目标 {{ store.config.target_water_class }} 类</span>
        <span>分析版本 {{ store.status.analysis_revision ?? 0 }} · 结果版本 {{ store.status.results_revision ?? 0 }}</span>
        <span v-if="store.status.data_identity?.kind==='synthetic'" class="data-kind">仿真数据</span>
      </div>
      <TextQuoteCard v-if="store.textQuestions?.pendingMain" :quote="store.textQuestions.pendingMain" removable @remove="store.textQuestions.pendingMain=null;store.questionContextNotice=''" />
      <QuestionContextCard v-if="store.questionContext" :context="store.questionContext" removable @remove="store.clearQuestionContext()" />
      <p v-if="!store.status.has_data && !store.questionContext" class="tip">从「项目与数据」载入监测表，或选择一段实际检索的资料、粘贴一张截图进行提问。</p>
      <p v-if="store.status.analysis_status === 'stale'" class="tip warning">检测或视频结果已更新，下方为历史对话；重新分析后可生成当前报告。</p>
      <p v-else-if="store.status.detection_outdated" class="tip warning">历史检测使用旧版证据口径，请重新分析。下方文字尚未按当前数据和走航时空限制复核。</p>
    </div></div>
    <div id="agent-progress"><AgentProgress :run="store.agentRun" :elapsed="elapsed" :connection-error="store.agentConnectionError" @stop="stop" @sync="refreshRun" /></div>
    <button v-if="lastRequest && !running && store.agentRun?.status!=='succeeded'" class="retry" @click="retryLast">从当前数据重新执行上一请求</button>
    <div class="conversation">
      <div ref="scroller" class="stream" role="region" aria-label="分析对话与报告" tabindex="0"
        @scroll.passive="scrollController.onScroll" @wheel.passive="readIntent" @keydown="readIntent" @pointerdown="readIntent" @touchstart.passive="readIntent">
        <div v-if="!messages.length" class="placeholder">
          <span class="eyebrow">河道研判工作台</span>
          <h3>从一个具体问题开始</h3>
          <p>查看异常、解释指标，或粘贴一张截图询问画面内容。</p>
          <div class="suggestions"><button v-for="question in suggestedQuestions" :key="question" :disabled="!canAsk || running" @click="input=question">{{ question }}<span aria-hidden="true">↗</span></button></div>
        </div>
        <ChatBubble v-for="(m, i) in messages" :key="i" :role="m.role" :content="m.content" :incomplete="m.incomplete" :evidence="m.evidence" :vision="m.vision" :question-context="m.question_context" :text-source="m.role==='assistant'?(m.text_source_id?{kind:'message',message_id:m.text_source_id}:m.run_id?{kind:'message',run_id:m.run_id}:null):null" @rendered="scrollDown" />
      </div>
      <div class="reading-controls"><span>{{ scrollState.following ? '跟随最新内容' : '已暂停跟随，可继续阅读' }}</span><button v-if="!scrollState.following" type="button" class="latest" @click="latest">{{ scrollState.hasUnseen ? '有新内容 · 回到最新' : '回到最新' }} <span aria-hidden="true">↓</span></button></div>
    </div>
    <p v-if="aiNotice" class="tip warning" role="alert">{{aiNotice}} <button type="button" class="retry" @click="recheckConfig">刷新配置状态</button></p>
    <form class="composer" @submit.prevent="send">
      <textarea ref="composerEl" aria-label="向水质分析助手提问" v-model="input" rows="2" :disabled="!canAsk || !store.ready || store.loading || running"
        :placeholder="selectedKind==='image_asset' ? '说说你想从这张图片了解什么…' : '描述你想了解的异常、指标或证据…'" @keydown="composerKey" @input="autosize" @paste="onPaste"></textarea>
      <div class="composer-actions">
        <div class="composer-tools">
          <button type="button" class="attach" :disabled="!canAttach" :title="canAttach ? '添加或粘贴一张截图' : '当前任务结束后可添加图片'" @click="pickImage">图片</button>
          <input ref="fileInput" type="file" accept="image/png,image/jpeg,image/webp,image/bmp" hidden @change="onFileChange">
          <button v-if="attachmentBusy" type="button" class="attach" disabled>正在读取图片…</button>
          <button v-else-if="failedAttachment" type="button" class="attach retry-attach" @click="retryAttachment">重试读取图片</button>
          <span class="keyboard-hint">Enter 发送 · Shift+Enter 换行 · 可粘贴截图</span>
        </div>
        <span v-if="attachmentError" class="attach-error" role="alert">{{attachmentError}}</span>
        <button type="submit" :disabled="!input.trim() || running || !store.ready || store.loading || !canAsk">发送 <span aria-hidden="true">↑</span></button>
      </div>
    </form>
  </Panel>
  <Panel class="followup-workspace" title="排查与复测" subtitle="保存候选假设、核对依据和人工复测记录">
    <details ref="investigationDetails" class="investigation-details" @toggle="openInvestigation"><summary>查看或新建排查任务</summary><InvestigationBoard v-if="investigationVisited" /></details>
  </Panel>
</template>
<style scoped>
.primary{display:inline-flex;align-items:center;gap:8px;background:var(--c-accent);color:#07222f;border:1px solid transparent;padding:10px 16px;border-radius:var(--radius-sm);font-weight:650;font-size:13px;min-height:42px}.primary svg{width:17px;height:17px;fill:none;stroke:currentColor;stroke-width:1.4}.primary:disabled{opacity:.45;cursor:not-allowed}
.context-strip{flex-shrink:0;border-bottom:1px solid var(--c-border-soft);margin-bottom:8px;display:flex;flex-wrap:wrap;align-items:center;gap:8px 16px;color:var(--c-sub);font-size:13px;padding-bottom:6px}.project-tag{color:var(--c-ink);font-weight:600;max-width:100%;overflow-wrap:anywhere}.data-kind{color:#73d5be;border:1px solid #2d635a;border-radius:5px;padding:1px 6px}
.chat-summary{flex-shrink:0;display:flex;align-items:center;gap:8px 12px;flex-wrap:wrap;min-height:28px;padding:1px 0 2px;color:var(--c-sub);font-size:12px;overflow-wrap:anywhere}
.chat-summary .project-tag{max-width:min(42ch,60%);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.summary-meta{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}
.workbench{flex-shrink:0;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin:2px 0 8px}
.wb-cell{min-width:0;display:flex;flex-direction:column;gap:3px;padding:8px 10px;border:1px solid var(--c-border-soft);border-radius:var(--radius-sm);background:var(--c-surface-2)}
.wb-label{font-size:12px;color:var(--c-muted);letter-spacing:.04em}
.wb-value{font-size:13px;font-weight:600;color:var(--c-ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.wb-action{align-self:flex-start;border:0;background:transparent;color:var(--c-accent);font:inherit;font-size:12px;padding:2px 0;min-height:24px;text-decoration:none;cursor:pointer}
.wb-action:hover:not(:disabled){text-decoration:underline}
.wb-action:disabled{opacity:.45;cursor:not-allowed}
.wb-wait{align-self:flex-start;font-size:12px;color:var(--c-muted);padding:2px 0;min-height:24px;line-height:24px}
@media(max-width:900px){.workbench{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:600px){.workbench{gap:6px;margin-bottom:6px}.wb-cell{padding:7px 8px}}
.vision-chip{color:var(--c-accent);border:1px solid var(--c-border);border-radius:12px;padding:1px 8px;font-size:12px;white-space:nowrap}
.info-toggle{margin-left:auto;flex-shrink:0;border:1px solid var(--c-border);background:var(--c-surface-2);color:var(--c-ink);border-radius:20px;padding:3px 10px;font:inherit;font-size:12px;min-height:26px;white-space:nowrap}
.info-toggle+.info-toggle{margin-left:0}
.info-toggle:disabled{opacity:.5}
.info-toggle[aria-expanded=true]{border-color:var(--c-accent);color:var(--c-accent)}
.context-line{flex-shrink:0;display:flex;align-items:center;gap:8px;min-height:28px;padding:2px 0;font-size:12px;color:var(--c-sub);min-width:0}
.context-kind{flex-shrink:0;color:var(--c-accent);border:1px solid var(--c-border);border-radius:10px;padding:0 7px;font-size:12px;line-height:20px}
.context-name{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--c-ink)}
.context-remove{margin-left:auto;flex-shrink:0;border:0;background:transparent;color:var(--c-sub);font:inherit;font-size:12px;padding:3px 4px;min-height:24px}
.analysis-info{flex-shrink:0;max-height:40vh;overflow:auto;overscroll-behavior:contain;border-bottom:1px solid var(--c-border-soft);margin-bottom:6px;padding-bottom:8px;min-height:0}
.tip{margin:0 0 16px;padding:12px 14px;background:var(--c-surface-2);border-radius:var(--radius-sm);color:var(--c-sub);font-size:14px}.warning{border-left:3px solid var(--sev-mild)}
.conversation{position:relative;display:flex;flex-direction:column;flex:1;min-height:0}.stream{flex:1;min-height:0;max-height:none;overflow-y:auto;overscroll-behavior:contain;scrollbar-gutter:stable;padding:6px 12px 20px 2px;display:flex;flex-direction:column;gap:28px;overflow-anchor:none}
.reading-controls{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:30px;padding:2px;color:var(--c-sub);font-size:12px}.latest{white-space:nowrap;border:1px solid var(--c-border);border-radius:20px;background:var(--c-surface-2);color:var(--c-ink);padding:5px 12px;font-size:12px;min-height:30px}
.placeholder{padding:38px 12px;max-width:620px;width:100%;margin:auto;color:var(--c-sub)}.eyebrow{font-size:12px;letter-spacing:.12em;color:var(--c-accent)}.placeholder h3{font-size:25px;font-weight:600;color:var(--c-ink);margin:8px 0}.placeholder p{font-size:15px;margin:0 0 24px;line-height:1.8}
.suggestions{display:grid;gap:8px}.suggestions button{text-align:left;padding:12px 14px;border:1px solid var(--c-border);border-radius:var(--radius-sm);background:transparent;color:var(--c-ink);font-size:14px;display:flex;align-items:center;justify-content:space-between;gap:12px;line-height:1.6}.suggestions button:hover:not(:disabled){border-color:var(--c-accent);background:var(--c-surface-2)}.suggestions button:disabled{opacity:.45}
.composer{margin-top:6px;border:1px solid var(--c-border);border-radius:var(--radius-md);background:var(--c-surface-2);padding:5px 10px;transition:border-color var(--duration-fast)}.composer:focus-within{border-color:var(--c-accent);box-shadow:0 0 0 2px #7cd4eb12}.composer textarea{display:block;resize:none;width:100%;min-height:52px;max-height:140px;overflow-y:auto;background:transparent;border:0;outline:0;color:var(--c-ink);padding:4px;font:inherit;font-size:15px;line-height:1.65}.composer-actions{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}.composer-tools{display:flex;align-items:center;gap:8px;min-width:0;flex-wrap:wrap}.keyboard-hint{font-size:12px;color:var(--c-sub)}.attach{background:transparent;color:var(--c-sub);border:1px solid var(--c-border);border-radius:6px;padding:4px 10px;font:inherit;font-size:12px;min-height:30px}.attach:hover:not(:disabled){border-color:var(--c-accent);color:var(--c-ink)}.attach:disabled{opacity:.5}.attach-error{color:#ffb7a7;font-size:12px;flex-basis:100%;order:3;margin:0}
.composer button{display:flex;align-items:center;gap:12px;background:var(--c-accent);color:#07222f;border:0;padding:6px 15px;border-radius:var(--radius-sm);font-weight:650;min-height:32px}.composer button:disabled{opacity:.4;cursor:not-allowed}
.investigation-details>summary{cursor:pointer;color:var(--c-ink);font-size:13px;padding:2px 0}.investigation-details[open]>summary{margin-bottom:18px}.retry{margin-bottom:12px;border:1px solid var(--c-border);background:var(--c-surface-2);color:var(--c-ink);border-radius:var(--radius-sm);padding:8px 12px}
@media(max-width:600px){.stream{gap:16px;padding-right:2px}.context-strip{gap:6px 12px}.primary{min-height:40px;padding:8px 12px}.placeholder{padding:24px 4px}.placeholder h3{font-size:22px}.keyboard-hint{font-size:12px}.reading-controls{font-size:12px}.latest{font-size:12px}}

/* 工作区高度跟随视口；正文 flex:1 且 min-height:0，不再用 580px 固定高度撑坏矮屏。 */
.chat-workspace{display:flex;flex-direction:column;height:calc(100dvh - 122px);min-height:0}
.chat-workspace > :deep(.head){flex-shrink:0;padding:10px 20px}
.chat-workspace > :deep(.body){display:flex;flex-direction:column;flex:1;min-height:0;overflow:hidden;padding:8px 20px 10px}
.chat-workspace :deep(.progress){flex-shrink:0}
.retry,.reading-controls,.composer{flex-shrink:0}
@media(max-width:1023px){.chat-workspace{height:calc(100dvh - 104px)}}
@media(max-width:600px){.chat-workspace{height:calc(100dvh - 96px)}.chat-workspace > :deep(.head),.chat-workspace > :deep(.body){padding:8px 14px}.conversation{min-height:0}.summary-meta{display:none}.keyboard-hint{display:none}.info-toggle{padding:5px 10px}}
@media(max-height:520px){.chat-workspace{height:calc(100dvh - 88px)}.chat-workspace > :deep(.head){padding:4px 14px}.chat-workspace > :deep(.body){padding:4px 14px;position:relative}.chat-summary{min-height:26px;padding:1px 0 2px;position:relative;z-index:7;background:var(--c-surface)}.info-toggle{min-height:26px;padding:3px 10px}.conversation{min-height:0}.placeholder{display:none}.reading-controls{min-height:26px;padding:2px}.composer{padding:5px 8px}.composer textarea{min-height:40px;max-height:96px}.analysis-info{position:absolute;left:0;right:0;top:36px;bottom:0;max-height:none;margin:0;padding:10px 14px;background:var(--c-surface);border-top:1px solid var(--c-border);z-index:6}}
</style>
