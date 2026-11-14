<script setup>
/**
 * 技术展示页 —— 对应 app.py 的 tab4。
 *
 * 原版是 6 个 st.expander，每块含说明 + 一键运行 + 结果展示。
 * 这里改为卡片流，交互逻辑一致：**无需对话即可单独体验每项能力**。
 * 这是给评审最快看懂"这个系统能做什么"的一页。
 */
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useSessionStore } from '../stores/session'
import { toastErr, toastOk } from '../composables/useToast'
import Panel from '../components/Panel.vue'
import StatusBanner from '../components/StatusBanner.vue'
import SummaryCards from '../components/SummaryCards.vue'
import BlackSpotResult from '../components/BlackSpotResult.vue'
import StationQuery from '../components/StationQuery.vue'
import MultiSourceTrace from '../components/MultiSourceTrace.vue'
import QuoteText from '../components/QuoteText.vue'
import RagSearch from '../components/RagSearch.vue'
import WaterQualityGridTech from '../components/WaterQualityGridTech.vue'
import InverseValidationTech from '../components/InverseValidationTech.vue'
import RagEvaluationTech from '../components/RagEvaluationTech.vue'
import ContextSourceFocus from '../components/ContextSourceFocus.vue'

const store = useSessionStore()
const route = useRoute()
const running = ref(false)
const summary = computed(() => store.blackSpots)
const toolEntries = [
  { id: 'station-query', title: '国控站点查询', description: '查看站点位置和公开水质监测数据' },
  { id: 'black-spot-detect', title: '黑臭筛查', description: '筛查走航记录中的异常点和热点' },
  { id: 'multi-source-trace', title: '多源溯源排查', description: '按位置查询周边潜在排查线索' },
  { id: 'rag-search', title: '治理案例检索', description: '搜索治理资料并查看相关原文' },
  { id: 'water-quality-grid', title: '水质正演模型', description: '查看水动力、扩散和网格验证结果' },
  { id: 'single-source-validation', title: '单源反演验证', description: '查看受控算例及源头判断条件' },
  { id: 'data-overview', title: '数据统计概览', description: '查看当前监测指标的均值和极值' },
]
const sourceTools = { '#rag_help': 'rag-search', '#grid_help': 'water-quality-grid' }
const activeTool = computed(() => toolEntries.find(entry => entry.id === (sourceTools[route.hash] || route.hash.slice(1))))
const toolView = ref(null)
watch(activeTool, () => {
  if (!route.query.context_id && !route.query.text_context) toolView.value?.focus({ preventScroll: true })
}, { flush: 'post' })

async function runBlackSpots() {
  running.value = true
  try {
    const res = await store.runBlackSpots()
    toastOk(`黑臭检测完成：检出 ${res.result['黑点总数']} 个黑点`)
  } catch (e) {
    toastErr(`检测失败：${e.message}`)
  } finally {
    running.value = false
  }
}
</script>

<template>
  <ContextSourceFocus :kinds="['rag_excerpt']" />
  <Panel :id="activeTool?.id" class="tool-panel" title="工具" :subtitle="activeTool ? '' : '选择一项工具打开。'">
    <template v-if="activeTool" #actions>
      <RouterLink class="tool-back" :to="{ name: 'tech' }">← 返回工具列表</RouterLink>
    </template>

    <nav v-if="!activeTool" ref="toolView" class="capability-index" aria-label="工具入口" tabindex="-1">
      <RouterLink v-for="entry in toolEntries" :key="entry.id" class="tool-entry" :to="{ name: 'tech', hash: `#${entry.id}` }">
        <span class="tool-entry-head"><strong>{{ entry.title }}</strong><span aria-hidden="true">→</span></span>
        <span class="tool-entry-desc">{{ entry.description }}</span>
      </RouterLink>
    </nav>

    <div v-else ref="toolView" role="region" :aria-label="activeTool.title" tabindex="-1">
      <StatusBanner />
      <p v-for="message in store.resultsWarnings" :key="message" class="hint">{{ message }}</p>
      <section v-if="activeTool.id === 'station-query'" class="block">
        <div class="block-head"><h2 class="bt">国控水质站点实时查询</h2><span class="scope-tag">无需上传数据</span></div>
        <p class="desc">
          查询生态环境部国控站点公开数据。实时请求失败时使用离线坐标库作位置参考，
          各次查询显示实际来源、数据时间及可用状态，离线坐标不当作实时监测值。
        </p>
        <StationQuery />
      </section>

      <section v-else-if="activeTool.id === 'black-spot-detect'" class="block">
        <div class="block-head"><h2 class="bt">多维黑臭点智能检测</h2><span class="scope-tag">基于已加载监测数据</span></div>
        <p class="desc">
          <b>4 维增强评分</b>：内梅罗综合指数 × 严重度因子 × 时间持续性 × 空间连续性。
          观测时长和空间连续性用于排序，孤立点仍保留阈值预警。
          使用米制球面距离和时间邻域聚类；氨氮、DO仅作点级筛查，
          工程综合指数与增强排序评分分别显示。
        </p>
        <button class="run" :disabled="!store.canAnalyze || !store.ready || store.loading || store.chatRunning || running" @click="runBlackSpots">
          {{ running ? '检测中…' : '运行黑臭检测' }}
        </button>
        <p v-if="!store.canAnalyze" class="hint">⚠️ 请先在左侧上传走航监测数据并点击「加载数据」</p>
        <BlackSpotResult v-if="summary" :data="summary" />
      </section>

      <section v-else-if="activeTool.id === 'multi-source-trace'" class="block">
        <div class="block-head"><h2 class="bt">多渠道污染溯源</h2><span class="scope-tag">排查线索 · 各源独立降级</span></div>
        <p class="desc">
          整合 <b>3 路真实数据源</b>加权融合去重：高德 POI（工厂/养殖场/排污口）、
          全国排污许可证管理信息平台、国控水质站点。各源独立查询、独立降级，
          任一源失败不阻断整体流程。
        </p>
        <MultiSourceTrace />
      </section>

      <section v-else-if="activeTool.id === 'rag-search'" class="block">
        <div class="block-head"><h2 class="bt">RAG 知识库智能检索</h2><span class="scope-tag">本地治理资料</span></div>
        <p class="desc">
          本地整理的治理资料经 text2vec-base-chinese 嵌入存入 ChromaDB，
          结合向量与词面检索，按来源合并，再选择与问题相关的原文片段。
          对话和报告使用同一检索内核；具体引用带片段编号，原始发布内容仍需核验。
        </p>
        <QuoteText :source="{kind:'help',help_id:'rag_help'}" source-id="rag_help" title="资料检索说明"><p class="desc">本地整理的治理资料结合向量与词面检索，按来源合并并返回相关原文片段。相似度不代表治理效果或责任概率；历史案例不能证明当前河段的污染责任。</p></QuoteText>
        <RagSearch />
        <RagEvaluationTech />
      </section>

      <section v-else-if="activeTool.id === 'water-quality-grid'" class="block">
        <div class="block-head"><h2 class="bt">水动力–水质模型与独立网格</h2><span class="scope-tag">恒定流情景</span></div>
        <WaterQualityGridTech />
      </section>

      <section v-else-if="activeTool.id === 'single-source-validation'" class="block">
        <div class="block-head"><h2 class="bt">受控单源反演与拒绝机制</h2><span class="scope-tag">离线受控验证</span></div>
        <InverseValidationTech />
      </section>

      <section v-else-if="activeTool.id === 'data-overview'" class="block">
        <div class="block-head"><h2 class="bt">数据统计概览</h2><span class="scope-tag">当前会话</span></div>
        <p class="desc">当前会话已加载数据的指标均值与极值。</p>
        <SummaryCards />
      </section>
    </div>
  </Panel>
</template>

<style scoped>
.capability-index {
  display:grid; grid-template-columns:repeat(auto-fill, minmax(min(100%, max(168px, calc((100% - 36px) / 4))), 1fr));
  grid-auto-rows:1fr;
  gap:12px;
}
.tool-entry {
  display:flex; flex-direction:column; gap:9px; min-height:98px; padding:14px;
  background:var(--c-surface-2); border:1px solid var(--c-border);
  border-radius:var(--radius-sm); color:var(--c-ink); text-decoration:none;
}
.tool-entry:hover, .tool-entry:focus-visible { border-color:var(--c-accent); background:var(--c-accent-soft); }
.tool-entry-head { display:flex; justify-content:space-between; align-items:baseline; gap:8px; font-size:var(--font-body); }
.tool-entry-head > span { color:var(--c-accent); }
.tool-entry-desc { color:var(--c-sub); font-size:var(--font-label); line-height:1.5; }
.tool-back { display:inline-block; padding:8px 12px; border:1px solid var(--c-border); border-radius:var(--radius-sm); color:var(--c-ink); text-decoration:none; }
.tool-back:hover, .tool-back:focus-visible { border-color:var(--c-accent); background:var(--c-accent-soft); }
.tool-panel { scroll-margin-top:84px; }
.block { padding-top:20px; }
.block-head { display:flex; align-items:baseline; flex-wrap:wrap; justify-content:space-between; gap:8px 16px; margin-bottom:14px; }
.bt { display:flex; align-items:baseline; gap:10px; margin:0; font-size: 18px; font-weight:600; }
.scope-tag { font-size: var(--font-label); color:var(--c-sub); line-height:1.7; }
.desc { margin:0 0 16px; font-size: var(--font-body); color:var(--c-sub); line-height:1.9; max-width:860px; }
.desc b { color:var(--c-ink); }
.hint { margin:8px 0 12px; font-size: var(--font-label); color:var(--c-sub); line-height:1.8; }
.run { background:var(--c-accent); color:#06121c; border:none; min-height:40px; padding:9px 18px; border-radius:var(--radius-sm); font-weight:600; font-size: var(--font-body); }
.run:disabled { opacity:.45; cursor:not-allowed; }
@media(max-width:600px){.block-head { gap:6px; }.bt { font-size: var(--font-content); }.capability-index { grid-template-columns:repeat(auto-fill, minmax(min(100%, 130px), 1fr)); gap:8px; }.tool-entry { padding:11px; }.tool-entry-head { font-size:var(--font-body); }}
</style>
