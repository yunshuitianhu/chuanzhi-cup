<script setup>
/**
 * 技术展示页 —— 对应 app.py 的 tab4。
 *
 * 原版是 6 个 st.expander，每块含说明 + 一键运行 + 结果展示。
 * 这里改为卡片流，交互逻辑一致：**无需对话即可单独体验每项能力**。
 * 这是给评审最快看懂"这个系统能做什么"的一页。
 */
import { computed, ref } from 'vue'
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
const running = ref(false)
const summary = computed(() => store.blackSpots)

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
  <Panel title="核心技术能力展示" subtitle="独立体验分析能力，了解模型原理与验证依据">
    <StatusBanner />
    <p v-for="message in store.resultsWarnings" :key="message" class="hint">{{ message }}</p>

    <section class="block">
      <h2 class="bt">📡 国控水质站点实时查询</h2>
      <p class="desc">
        查询生态环境部国控站点公开数据。实时请求失败时使用离线坐标库作位置参考，
        各次查询显示实际来源、数据时间及可用状态，离线坐标不当作实时监测值。
      </p>
      <p class="hint">💡 无需上传数据即可体验</p>
      <StationQuery />
    </section>

    <section class="block">
      <h2 class="bt">🔍 多维黑臭点智能检测</h2>
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

    <section class="block">
      <h2 class="bt">🎯 多渠道污染溯源</h2>
      <p class="desc">
        整合 <b>3 路真实数据源</b>加权融合去重：高德 POI（工厂/养殖场/排污口）、
        全国排污许可证管理信息平台、国控水质站点。各源独立查询、独立降级，
        任一源失败不阻断整体流程。
      </p>
      <MultiSourceTrace />
    </section>

    <section class="block" id="rag-search">
      <h2 class="bt">📚 RAG 知识库智能检索</h2>
      <p class="desc">
        本地整理的治理资料经 text2vec-base-chinese 嵌入存入 ChromaDB，
        结合向量与词面检索，按来源合并，再选择与问题相关的原文片段。
        对话和报告使用同一检索内核；具体引用带片段编号，原始发布内容仍需核验。
      </p>
      <QuoteText :source="{kind:'help',help_id:'rag_help'}" source-id="rag_help" title="资料检索说明"><p class="desc">本地整理的治理资料结合向量与词面检索，按来源合并并返回相关原文片段。相似度不代表治理效果或责任概率；历史案例不能证明当前河段的污染责任。</p></QuoteText>
      <RagSearch />
      <RagEvaluationTech />
    </section>

    <section class="block" id="water-quality-grid">
      <h2 class="bt">🌊 水动力–水质模型与独立网格</h2>
      <WaterQualityGridTech />
    </section>

    <section class="block" id="single-source-validation">
      <h2 class="bt">🧪 受控单源反演与拒绝机制</h2>
      <InverseValidationTech />
    </section>

    <section class="block last">
      <h2 class="bt">📊 数据统计概览</h2>
      <p class="desc">当前会话已加载数据的指标均值与极值。</p>
      <SummaryCards />
    </section>

  </Panel>
</template>

<style scoped>
.block { padding: 20px 0; border-bottom: 1px solid var(--c-border); }
.block.last { border-bottom: none; }
.bt { margin: 0 0 8px; font-size: 15px; font-weight: 600; }
.desc { margin: 0 0 10px; font-size: 12px; color: var(--c-sub); line-height: 1.9; max-width: 820px; }
.desc b { color: var(--c-ink); }
.hint { margin: 6px 0 10px; font-size: 11px; color: var(--c-sub); }

.run {
  background: var(--c-accent); color: #06121c; border: none;
  padding: 8px 18px; border-radius: 7px; font-weight: 600; font-size: 13px;
}
.run:disabled { opacity: .45; cursor: not-allowed; }
</style>
