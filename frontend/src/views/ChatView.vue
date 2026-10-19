<script setup>
/**
 * 智能对话页 —— 对应 app.py 的 tab1。
 *
 * 「开始自动分析」会让后端 Agent 自主编排整条工具链（12+6 步 SOP），
 * 与自由对话共用同一套 Function Calling 循环。
 */
import { nextTick, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useSessionStore } from '../stores/session'
import { sseHeaders, adoptSessionFrom } from '../api/client'
import { toastErr } from '../composables/useToast'
import Panel from '../components/Panel.vue'
import ChatBubble from '../components/ChatBubble.vue'

const store = useSessionStore()
const input = ref('')
const { chatRunning: running, chatMessages: messages } = storeToRefs(store)
const scroller = ref(null)

async function scrollDown() {
  await nextTick()
  if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight
}

async function autoAnalyze() {
  if (running.value) return
  running.value = true
  messages.value.push({ role: 'user', content: '请开始自动分析。' })
  scrollDown()
  try {
    const res = await fetch('/api/agent/chat', {
      method: 'POST',
      headers: sseHeaders(),        // 含 X-Session-Id，否则后端会新建空会话
      body: JSON.stringify({ message: '请开始自动分析。', auto: true }),
    })
    if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`)
    adoptSessionFrom(res)
    await consume(res)
  } catch (e) {
    toastErr(`分析失败：${e.message}`)
  } finally {
    running.value = false
    await store.refreshStatus().catch((e) => toastErr(e.message))
  }
}

async function send() {
  const text = input.value.trim()
  if (!text || running.value) return
  input.value = ''
  running.value = true
  messages.value.push({ role: 'user', content: text })
  scrollDown()
  try {
    const res = await fetch('/api/agent/chat', {
      method: 'POST',
      headers: sseHeaders(),        // 含 X-Session-Id，否则后端会新建空会话
      body: JSON.stringify({ message: text }),
    })
    if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`)
    adoptSessionFrom(res)
    await consume(res)
  } catch (e) {
    toastErr(`对话失败：${e.message}`)
  } finally {
    running.value = false
    await store.refreshStatus().catch((e) => toastErr(e.message))
  }
}

/** 解析 SSE 流：tool 事件插入工具调用提示，delta 事件累积回答。 */
async function consume(res) {
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  let current = null

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })

    const chunks = buf.split('\n\n')
    buf = chunks.pop() || ''
    for (const chunk of chunks) {
      const ev = /^event: (\w+)/m.exec(chunk)?.[1]
      const dataLine = /^data: (.*)$/m.exec(chunk)?.[1]
      if (!ev || dataLine === undefined) continue
      let payload = null
      try { payload = JSON.parse(dataLine) } catch { /* 忽略非 JSON 帧 */ }

      if (ev === 'tool') {
        messages.value.push({ role: 'tool', content: payload?.name || '工具调用' })
        scrollDown()
      } else if (ev === 'tool_done') {
        messages.value.push({ role: payload?.ok ? 'tool' : 'error',
          content: `${payload?.name || '工具'}：${payload?.ok ? '完成' : '失败，请查看分析结论中的缺失说明'}` })
        current = null
        scrollDown()
      } else if (ev === 'delta') {
        if (!current) {
          messages.value.push({ role: 'assistant', content: '' })
          current = messages.value[messages.value.length - 1]
        }
        current.content += payload?.text || ''
        scrollDown()
      } else if (ev === 'done') {
        current = null
        if (payload?.status === 'partial' || payload?.status === 'failed') {
          messages.value.push({ role: 'error', content: payload.status === 'partial'
            ? '本轮仅部分完成，请查看失败或缺失的分析步骤。'
            : '本轮未通过完成校验，不能视为分析成功。' })
        }
        await store.refreshStatus()
      } else if (ev === 'error') {
        messages.value.push({ role: 'error', content: payload?.message || '未知错误' })
        scrollDown()
      }
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

    <div ref="scroller" class="stream">
      <div v-if="!messages.length" class="placeholder">
        点击右上角「开始自动分析」，或直接在下方提问。<br />
        <span class="ex">例如：「3 号聚类的主要污染物是什么？」「帮我对比上午和下午的氨氮分布」</span>
      </div>
      <ChatBubble v-for="(m, i) in messages" :key="i" :role="m.role" :content="m.content" />
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
