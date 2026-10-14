<script setup>
/**
 * 技术展示页 —— 对应 app.py 的 tab4。
 *
 * 原版是 6 个 st.expander，每个含说明 + 一键运行按钮 + 结果展示。
 * 这里改为卡片网格，交互逻辑一致：无需对话即可单独体验每项能力。
 */
import { ref } from 'vue'
import { useSessionStore } from '../stores/session'
import { toastErr, toastOk } from '../composables/useToast'
import Panel from '../components/Panel.vue'
import StatusBanner from '../components/StatusBanner.vue'
import BlackSpotResult from '../components/BlackSpotResult.vue'
import SummaryCards from '../components/SummaryCards.vue'

const store = useSessionStore()
const running = ref(false)
const summary = ref(null)

async function runBlackSpots() {
  running.value = true
  try {
    const res = await store.runBlackSpots()
    summary.value = res
    toastOk(`黑臭检测完成：检出 ${res.result['黑点总数']} 个黑点`)
  } catch (e) {
    toastErr(`检测失败：${e.message}`)
  } finally {
    running.value = false
  }
}
</script>

<template>
  <Panel title="核心技术能力展示" subtitle="每项能力均可独立运行，无需通过对话提问">
    <StatusBanner />

    <section class="block">
      <h3 class="block-title">🔍 多维黑臭点智能检测</h3>
      <p class="desc">
        内梅罗综合指数 × 严重度因子 × 时间持续性 × 空间连续性 —— 四维增强评分，
        再用 DBSCAN 聚类定位热点区域。孤立单点超标被抑制，持续成片超标被放大。
      </p>
      <button class="run" :disabled="!store.canAnalyze || running" @click="runBlackSpots">
        {{ running ? '检测中…' : '运行黑臭检测' }}
      </button>
      <p v-if="!store.canAnalyze" class="tip">需先上传走航数据</p>
      <BlackSpotResult v-if="summary" :data="summary" />
    </section>

    <section class="block">
      <h3 class="block-title">📊 数据统计概览</h3>
      <SummaryCards />
    </section>

    <section class="block muted">
      <h3 class="block-title">其余能力（接入中）</h3>
      <ul class="todo">
        <li>📡 国控水质站点实时查询 — 等待 <code>/api/stations</code></li>
        <li>🎯 多渠道污染溯源 — 等待 <code>/api/trace/*</code></li>
        <li>📚 RAG 知识库检索 — 等待 <code>/api/rag/*</code></li>
        <li>🌊 河道模型参数检测 — 等待 <code>/api/river/*</code></li>
      </ul>
    </section>
  </Panel>
</template>

<style scoped>
.block { padding: 18px 0; border-bottom: 1px solid var(--c-border); }
.block:last-child { border-bottom: none; }
.block.muted { opacity: .75; }
.block-title { margin: 0 0 6px; font-size: 14px; }
.desc { margin: 0 0 12px; font-size: 12px; color: var(--c-sub); line-height: 1.8; max-width: 760px; }

.run {
  background: var(--c-accent); color: #06121c; border: none;
  padding: 8px 18px; border-radius: 7px; font-weight: 600; font-size: 13px;
}
.run:disabled { opacity: .45; cursor: not-allowed; }
.tip { margin: 8px 0 0; font-size: 12px; color: var(--c-sub); }

.todo { margin: 8px 0 0; padding-left: 18px; font-size: 12px; color: var(--c-sub); line-height: 2; }
code { background: #0d151d; padding: 1px 5px; border-radius: 3px; }
</style>
