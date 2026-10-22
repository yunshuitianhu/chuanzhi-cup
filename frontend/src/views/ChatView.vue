<script setup>
/**
 * 智能对话页 —— 对应 app.py 的 tab1。
 *
 * 「开始自动分析」会让后端 Agent 自主编排整条工具链（12+6 步 SOP），
 * 与自由对话共用同一套 Function Calling 循环。
 */
import { nextTick, ref, computed, onMounted, onUnmounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useSessionStore } from '../stores/session'
import { sseHeaders, adoptSessionFrom } from '../api/client'
import { toastErr } from '../composables/useToast'
import Panel from '../components/Panel.vue'
import ChatBubble from '../components/ChatBubble.vue'
import AgentProgress from '../components/AgentProgress.vue'
import { consumeAgentStream } from '../utils/agentStream'

const store = useSessionStore()
const input = ref('')
const { chatRunning: running, chatMessages: messages } = storeToRefs(store)
const scroller = ref(null)
const clock=ref(Date.now())
const lastRequest=ref(null)
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
  refreshRun()
  let ticks=0
  timer=setInterval(()=>{clock.value=Date.now();if(++ticks%2===0 && store.agentRun?.busy)refreshRun()},1000)
})
onUnmounted(()=>clearInterval(timer))

async function scrollDown() {
  await nextTick()
  if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight
}

async function autoAnalyze() {
  return submit('请开始自动分析。',true)
}

async function send() {
  const text = input.value.trim()
  if (!text || running.value) return
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
  scrollDown()
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
  <Panel title="智能对话" subtitle="DeepSeek Agent 自主编排分析工具链">
    <template #actions>
      <button class="primary" :disabled="!store.canAnalyze || !store.ready || store.loading || running" @click="autoAnalyze">
        {{ running ? '分析中…' : '🚀 开始自动分析' }}
      </button>
    </template>

    <div v-if="!store.status.has_data" class="tip">请先在左侧上传并加载走航数据。</div>
    <div v-if="store.status.analysis_status === 'stale'" class="tip">检测或视频结果已更新，下方保留的是历史对话；请重新分析以生成当前报告。</div>
    <div v-else-if="store.status.detection_outdated" class="tip">历史检测与报告使用旧版证据口径，请重新分析。历史文字尚未按当前数据身份和走航时空限制复核。</div>
    <AgentProgress :run="store.agentRun" :elapsed="elapsed" :connection-error="store.agentConnectionError" @stop="stop" @sync="refreshRun" />
    <button v-if="lastRequest && !running && store.agentRun?.status!=='succeeded'" class="retry" @click="submit(lastRequest.text,lastRequest.auto)">从当前数据重新执行上一请求</button>

    <div ref="scroller" class="stream">
      <div v-if="!messages.length" class="placeholder">
        点击右上角「开始自动分析」，或直接在下方提问。<br />
        <span class="ex">例如：「3 号聚类的主要污染物是什么？」「帮我对比上午和下午的氨氮分布」</span>
      </div>
      <ChatBubble v-for="(m, i) in messages" :key="i" :role="m.role" :content="m.content" :incomplete="m.incomplete" :evidence="m.evidence" />
    </div>

    <form class="composer" @submit.prevent="send">
      <input
        v-model="input"
        :disabled="!store.status.has_data || !store.ready || store.loading || running"
        placeholder="请输入问题…"
      />
      <button type="submit" :disabled="!input.trim() || running">发送</button>
    </form>
  </Panel>
</template>

<style scoped>
.primary {
  background: var(--c-accent); color: #06121c; border: none;
  padding: 7px 16px; border-radius: 7px; font-weight: 600; font-size: 13px;
}
.primary:disabled { opacity: .45; cursor: not-allowed; }

.tip { padding: 20px; text-align: center; color: var(--c-sub); font-size: 13px; }

.stream {
  min-height: 340px; max-height: 58vh; overflow-y: auto;
  padding: 4px 2px 12px; display: flex; flex-direction: column; gap: 10px;
}
.placeholder { color: var(--c-sub); font-size: 13px; text-align: center; padding: 60px 20px; line-height: 2; }
.ex { font-size: 12px; opacity: .75; }

.composer { display: flex; gap: 8px; border-top: 1px solid var(--c-border); padding-top: 12px; }
.composer input {
  flex: 1; background: var(--c-surface-2); border: 1px solid var(--c-border);
  color: var(--c-ink); border-radius: 8px; padding: 10px 14px; font-family: inherit; font-size: 13px;
}
.composer input:focus { outline: none; border-color: var(--c-accent); }
.composer button {
  background: var(--c-accent); color: #06121c; border: none;
  padding: 0 22px; border-radius: 8px; font-weight: 600;
}
.composer button:disabled { opacity: .4; cursor: not-allowed; }
</style>
