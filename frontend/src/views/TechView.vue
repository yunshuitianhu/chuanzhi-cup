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

    <nav class="capability-index" aria-label="技术能力目录">
      <a href="#station-query">站点查询</a><a href="#black-spot-detect">黑臭筛查</a>
      <a href="#multi-source-trace">多源排查</a><a href="#rag-search">资料检索</a>
      <a href="#water-quality-grid">正演模型</a><a href="#single-source-validation">反演验证</a>
      <a href="#data-overview">数据概览</a>
    </nav>

    <section class="block" id="station-query">
      <div class="block-head"><h2 class="bt"><span class="capability-number" aria-hidden="true">01</span>国控水质站点实时查询</h2><span class="scope-tag">无需上传数据</span></div>
      <p class="desc">
        查询生态环境部国控站点公开数据。实时请求失败时使用离线坐标库作位置参考，
        各次查询显示实际来源、数据时间及可用状态，离线坐标不当作实时监测值。
      </p>
      <StationQuery />
    </section>

    <section class="block" id="black-spot-detect">
      <div class="block-head"><h2 class="bt"><span class="capability-number" aria-hidden="true">02</span>多维黑臭点智能检测</h2><span class="scope-tag">基于已加载监测数据</span></div>
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

    <section class="block" id="multi-source-trace">
      <div class="block-head"><h2 class="bt"><span class="capability-number" aria-hidden="true">03</span>多渠道污染溯源</h2><span class="scope-tag">排查线索 · 各源独立降级</span></div>
      <p class="desc">
        整合 <b>3 路真实数据源</b>加权融合去重：高德 POI（工厂/养殖场/排污口）、
        全国排污许可证管理信息平台、国控水质站点。各源独立查询、独立降级，
        任一源失败不阻断整体流程。
      </p>
      <MultiSourceTrace />
    </section>

    <section class="block" id="rag-search">
      <div class="block-head"><h2 class="bt"><span class="capability-number" aria-hidden="true">04</span>RAG 知识库智能检索</h2><span class="scope-tag">本地治理资料</span></div>
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
      <div class="block-head"><h2 class="bt"><span class="capability-number" aria-hidden="true">05</span>水动力–水质模型与独立网格</h2><span class="scope-tag">恒定流情景</span></div>
      <WaterQualityGridTech />
    </section>

    <section class="block" id="single-source-validation">
      <div class="block-head"><h2 class="bt"><span class="capability-number" aria-hidden="true">06</span>受控单源反演与拒绝机制</h2><span class="scope-tag">离线受控验证</span></div>
      <InverseValidationTech />
    </section>

    <section class="block last" id="data-overview">
      <div class="block-head"><h2 class="bt"><span class="capability-number" aria-hidden="true">07</span>数据统计概览</h2><span class="scope-tag">当前会话</span></div>
      <p class="desc">当前会话已加载数据的指标均值与极值。</p>
      <SummaryCards />
    </section>

  </Panel>
</template>

<style scoped>
.capability-index { display:flex; flex-wrap:wrap; gap:8px; padding:2px 0 20px; border-bottom:1px solid var(--c-border); }
.capability-index a { padding:7px 12px; color:var(--c-sub); border:1px solid var(--c-border); border-radius:var(--radius-sm); font-size:12px; text-decoration:none; }
.capability-index a:hover { color:var(--c-ink); background:var(--c-surface-2); border-color:var(--c-accent); }
.block { padding:28px 0; border-bottom:1px solid var(--c-border); scroll-margin-top:84px; }
.block.last { border-bottom:none; }
.block-head { display:flex; align-items:baseline; flex-wrap:wrap; justify-content:space-between; gap:8px 16px; margin-bottom:14px; }
.bt { display:flex; align-items:baseline; gap:10px; margin:0; font-size:16px; font-weight:600; }
.capability-number { font-size:11px; font-weight:500; letter-spacing:.05em; color:var(--c-accent); }
.scope-tag { font-size:11px; color:var(--c-sub); line-height:1.7; }
.desc { margin:0 0 16px; font-size:13px; color:var(--c-sub); line-height:1.9; max-width:860px; }
.desc b { color:var(--c-ink); }
.hint { margin:8px 0 12px; font-size:12px; color:var(--c-sub); line-height:1.8; }
.run { background:var(--c-accent); color:#06121c; border:none; min-height:40px; padding:9px 18px; border-radius:var(--radius-sm); font-weight:600; font-size:13px; }
.run:disabled { opacity:.45; cursor:not-allowed; }
@media(max-width:600px){.block { padding:24px 0; }.block-head { gap:6px; }.bt { font-size:15px; }.scope-tag { padding-left:25px; }.capability-index { gap:6px; padding-bottom:16px; }.capability-index a { font-size:11px; padding:7px 10px; }}
</style>
