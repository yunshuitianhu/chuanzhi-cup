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
import AgentProgress from '../components/AgentProgress.vue'
import InvestigationBoard from '../components/InvestigationBoard.vue'
import { consumeAgentStream } from '../utils/agentStream'
import { createChatScroll } from '../utils/chatScroll'

const store = useSessionStore()
const input = ref('')
const { chatRunning: running, chatMessages: messages } = storeToRefs(store)
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

async function autoAnalyze() {
  return submit('请开始自动分析。',true)
}

async function send() {
  const text = input.value.trim()
  if (!text || running.value || !store.ready || store.loading || !store.status.has_data) return
  input.value = ''
  return submit(text,false)
}

async function submit(text,auto) {
  if(running.value || !store.ready || !store.status.has_data)return
  const workspace=store.workspaceRevision, project=store.projectState.current?.id
  const current=()=>workspace===store.workspaceRevision && project===store.projectState.current?.id
  const token=crypto.randomUUID()
  lastRequest.value={text,auto}
  if(!store.beginAgentStream())return
  messages.value.push({role:'user',content:text,uiRequest:token})
  latest()
  let runId=null, answer=null
  function incomplete() {
    for(const message of messages.value)if(message.uiRequest===token && message.role==='assistant')message.incomplete=true
  }
  try {
    const res = await fetch('/api/agent/chat', {
      method: 'POST',
      headers: sseHeaders(),
      body: JSON.stringify({ message:text, auto }),
    })
    if (!res.ok || !res.body) {
      const error=await res.json().catch(()=>({}))
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
      } else if(event==='delta') {
        if(!answer) {
          messages.value.push({role:'assistant',content:'',uiRequest:token})
          answer=messages.value[messages.value.length-1]
        }
        answer.content+=payload.text || ''
      } else if(event==='done' && payload.status!=='succeeded') {
        incomplete()
        messages.value.push({role:'error',uiRequest:token,content:payload.status==='cancelled'
          ? '本轮已停止。已完成的部分分析可保留；重新分析会从当前数据重新取证。'
          : '本轮未完整完成，请查看失败或缺失步骤后重新分析。'})
      } else if(event==='error') {
        incomplete()
        messages.value.push({role:'error',content:payload.message || '本轮执行失败',uiRequest:token})
      }
      scrollDown()
    })
  } catch (e) {
    if(current()) {
      incomplete()
      messages.value.push({role:'error',content:`${e.message}；请核对任务状态后重试。`,uiRequest:token})
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
  <Panel title="智能对话" subtitle="围绕监测数据提问，核对证据，形成复测计划">
    <template #actions>
      <button class="primary" :disabled="!store.canAnalyze || !store.ready || store.loading || running" :aria-busy="running" @click="autoAnalyze">
        <svg aria-hidden="true" viewBox="0 0 20 20"><path d="m10 2 1.8 5.7L18 10l-6.2 2.3L10 18l-1.8-5.7L2 10l6.2-2.3Z" /></svg>
        {{ running ? '分析中…' : '开始自动分析' }}
      </button>
    </template>
    <div class="context-strip" aria-label="当前分析上下文">
      <span class="project-tag">{{ store.projectState.current?.name || '临时巡航会话' }}</span>
      <span>{{ store.status.has_data ? store.status.rows + ' 条监测记录' : '等待载入监测数据' }}</span>
      <span>目标 {{ store.config.target_water_class }} 类</span>
      <span v-if="store.status.data_identity?.kind==='synthetic'" class="data-kind">仿真数据</span>
    </div>
    <p v-if="!store.status.has_data" class="tip">从「项目与数据」打开已保存项目，或上传走航监测表。</p>
    <p v-if="store.status.analysis_status === 'stale'" class="tip warning">检测或视频结果已更新，下方为历史对话；重新分析后可生成当前报告。</p>
    <p v-else-if="store.status.detection_outdated" class="tip warning">历史检测使用旧版证据口径，请重新分析。下方文字尚未按当前数据和走航时空限制复核。</p>
    <AgentProgress :run="store.agentRun" :elapsed="elapsed" :connection-error="store.agentConnectionError" @stop="stop" @sync="refreshRun" />
    <button v-if="lastRequest && !running && store.agentRun?.status!=='succeeded'" class="retry" @click="submit(lastRequest.text,lastRequest.auto)">从当前数据重新执行上一请求</button>
    <div class="conversation" :class="{'has-progress':!!store.agentRun?.run_id || !!store.agentConnectionError}">
      <div ref="scroller" class="stream" role="region" aria-label="分析对话与报告" tabindex="0"
        @scroll.passive="scrollController.onScroll" @wheel.passive="readIntent" @keydown="readIntent" @pointerdown="readIntent" @touchstart.passive="readIntent">
        <div v-if="!messages.length" class="placeholder">
          <span class="eyebrow">河道研判工作台</span>
          <h3>从一个具体问题开始</h3>
          <p>查看异常、解释指标，或核对下一步需要补充的证据。</p>
          <div class="suggestions"><button v-for="question in suggestedQuestions" :key="question" :disabled="!store.status.has_data || running" @click="input=question">{{ question }}<span aria-hidden="true">↗</span></button></div>
        </div>
        <ChatBubble v-for="(m, i) in messages" :key="i" :role="m.role" :content="m.content" :incomplete="m.incomplete" :evidence="m.evidence" @rendered="scrollDown" />
      </div>
      <div class="reading-controls"><span>{{ scrollState.following ? '跟随最新内容' : '已暂停跟随，可继续阅读' }}</span><button v-if="!scrollState.following" type="button" class="latest" @click="latest">{{ scrollState.hasUnseen ? '有新内容 · 回到最新' : '回到最新' }} <span aria-hidden="true">↓</span></button></div>
    </div>
    <form class="composer" @submit.prevent="send">
      <textarea aria-label="向水质分析助手提问" v-model="input" rows="2" :disabled="!store.status.has_data || !store.ready || store.loading || running"
        placeholder="描述你想了解的异常、指标或证据…" @keydown="composerKey"></textarea>
      <div class="composer-actions"><span class="keyboard-hint">Enter 发送 · Shift+Enter 换行</span><button type="submit" :disabled="!input.trim() || running || !store.ready || store.loading || !store.status.has_data">发送 <span aria-hidden="true">↑</span></button></div>
    </form>
  </Panel>
  <Panel title="排查与复测" subtitle="保存候选假设、核对依据和人工复测记录">
    <details ref="investigationDetails" class="investigation-details" @toggle="openInvestigation"><summary>查看或新建排查任务</summary><InvestigationBoard v-if="investigationVisited" /></details>
  </Panel>
</template>
<style scoped>
.primary{display:inline-flex;align-items:center;gap:8px;background:var(--c-accent);color:#07222f;border:1px solid transparent;padding:10px 16px;border-radius:var(--radius-sm);font-weight:650;font-size:13px;min-height:42px}.primary svg{width:17px;height:17px;fill:none;stroke:currentColor;stroke-width:1.4}.primary:disabled{opacity:.45;cursor:not-allowed}
.context-strip{display:flex;flex-wrap:wrap;align-items:center;gap:8px 16px;color:var(--c-sub);font-size:13px;padding-bottom:16px}.project-tag{color:var(--c-ink);font-weight:600;max-width:100%;overflow-wrap:anywhere}.data-kind{color:#73d5be;border:1px solid #2d635a;border-radius:5px;padding:1px 6px}
.tip{margin:0 0 16px;padding:12px 14px;background:var(--c-surface-2);border-radius:var(--radius-sm);color:var(--c-sub);font-size:13px}.warning{border-left:3px solid var(--sev-mild)}
.conversation{position:relative}.stream{min-height:260px;max-height:calc(100dvh - 420px);overflow-y:auto;overscroll-behavior:contain;scrollbar-gutter:stable;padding:4px 8px 18px 2px;display:flex;flex-direction:column;gap:20px;overflow-anchor:none}
.conversation.has-progress .stream{max-height:calc(100dvh - 530px)}
.reading-controls{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:40px;padding:6px 2px;color:var(--c-sub);font-size:12px}.latest{white-space:nowrap;border:1px solid var(--c-border);border-radius:20px;background:var(--c-surface-2);color:var(--c-ink);padding:5px 12px;font-size:12px;min-height:30px}
.placeholder{padding:38px 12px;max-width:620px;width:100%;margin:auto;color:var(--c-sub)}.eyebrow{font-size:11px;letter-spacing:.12em;color:var(--c-accent)}.placeholder h3{font-size:25px;font-weight:600;color:var(--c-ink);margin:8px 0}.placeholder p{font-size:14px;margin:0 0 24px;line-height:1.8}
.suggestions{display:grid;gap:8px}.suggestions button{text-align:left;padding:12px 14px;border:1px solid var(--c-border);border-radius:var(--radius-sm);background:transparent;color:var(--c-ink);font-size:13px;display:flex;align-items:center;justify-content:space-between;gap:12px;line-height:1.6}.suggestions button:hover:not(:disabled){border-color:var(--c-accent);background:var(--c-surface-2)}.suggestions button:disabled{opacity:.45}
.composer{margin-top:8px;border:1px solid var(--c-border);border-radius:var(--radius-md);background:var(--c-surface-2);padding:10px 12px;transition:border-color var(--duration-fast)}.composer:focus-within{border-color:var(--c-accent)}.composer textarea{display:block;resize:vertical;width:100%;min-height:62px;max-height:180px;background:transparent;border:0;outline:0;color:var(--c-ink);padding:4px;font:inherit;font-size:14px;line-height:1.65}.composer-actions{display:flex;align-items:center;justify-content:space-between;gap:10px}.keyboard-hint{font-size:11px;color:var(--c-sub)}
.composer button{display:flex;align-items:center;gap:12px;background:var(--c-accent);color:#07222f;border:0;padding:8px 15px;border-radius:var(--radius-sm);font-weight:650;min-height:36px}.composer button:disabled{opacity:.4;cursor:not-allowed}
.investigation-details>summary{cursor:pointer;color:var(--c-ink);font-size:13px;padding:2px 0}.investigation-details[open]>summary{margin-bottom:18px}.retry{margin-bottom:12px;border:1px solid var(--c-border);background:var(--c-surface-2);color:var(--c-ink);border-radius:var(--radius-sm);padding:8px 12px}
@media(max-width:600px){.conversation.has-progress .stream{min-height:160px;max-height:calc(100dvh - 570px)}.stream{min-height:180px;max-height:calc(100dvh - 440px);gap:16px;padding-right:2px}.context-strip{gap:6px 12px;padding-bottom:12px}.primary{min-height:40px;padding:8px 12px}.placeholder{padding:24px 4px}.placeholder h3{font-size:22px}.keyboard-hint{font-size:10px}.composer{padding:8px}.reading-controls{font-size:11px}.latest{font-size:11px}}
</style>
