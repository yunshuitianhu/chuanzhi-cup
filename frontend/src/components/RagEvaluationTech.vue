<script setup>
import report from '../data/ragEvaluation.json'
const modes=[['no_rag','无检索上下文'],['dense_v1','原向量检索＋短摘要'],['hybrid-rrf-v1','融合检索＋证据片段']]
const pct=value=>`${(value*100).toFixed(1)}%`
</script>

<template>
  <div class="rag-evaluation">
    <p class="scope"><strong>固定资料查找评测 · {{report.date}}</strong> · {{report.corpus_documents}}份资料 / {{report.chunks}}个分块</p>
    <p>{{report.questions}}道预先固定问题：开发集{{report.dev}}道、留出集{{report.holdout}}道。下表检索指标只统计留出集16道有目标资料的问题；4道缺资料问题另行保留。</p>
    <div class="table-scroll" role="region" tabindex="0" aria-label="RAG留出集评测对比">
      <table>
        <thead><tr><th>方案</th><th>前5命中</th><th>要点片段覆盖</th><th>回答要点</th><th>缺资料保留判断</th></tr></thead>
        <tbody><tr v-for="[key,label] in modes" :key="key"><th scope="row">{{label}}</th>
          <td>{{pct(report.retrieval[key].hit_at_5)}}</td><td>{{pct(report.retrieval[key].evidence_term_coverage)}}</td>
          <td>{{report.answer_evaluation[key].target_fact_answered_agent_review}} / {{report.answer_evaluation[key].positive_questions}}</td>
          <td>{{report.answer_evaluation[key].negative_abstentions}} / {{report.answer_evaluation[key].negative_questions}}</td>
        </tr></tbody>
      </table>
    </div>
    <p>回答对比实际调用DeepSeek 24次（三方案各8道，同提示、温度0、相同最大上下文预算）。原方案保留300字符摘要，评测时补上真实分块ID；新方案按来源合并并返回最多两个原文片段。无检索方案不提供资料，因此不能代表模型自由问答能力。</p>
    <details><summary>引文检查与未解决项</summary>
      <p>新方案7条引文中，6条符合严格原文匹配；另1条只省略Markdown粗体标记。去除成对格式标记后7条可匹配原文，但这不表示7条都回答了所问事实。</p>
      <ul><li v-for="failure in report.failures" :key="failure.id">{{failure.id}}：{{failure.query}} 缺少片段中的要点：{{failure.missed_terms.join('、')}}。</li></ul>
      <p>两项均找到了案例但没返回目标效果片段，模型也出现“可回答”标记与“资料未提供”正文不一致，计为未完成回答；保留失败，不用留出集继续调参。</p>
    </details>
    <p class="limit">标注与回答复核由Agent完成，尚无独立专家复核。只衡量本地资料查找与引文一致性；原始发布资料仍需核验，不是现场检测准确率、治理效果保证或污染责任认证。结果快照与当前上传数据无关。</p>
  </div>
</template>

<style scoped>
.rag-evaluation{font-size:12px;line-height:1.9;margin-top:20px;padding:14px;background:var(--c-surface-2);border:1px solid var(--c-border);border-radius:8px}.scope strong{color:var(--c-accent)}p{margin:8px 0}.table-scroll{overflow-x:auto}table{border-collapse:collapse;min-width:460px;width:100%;text-align:left}th,td{padding:9px;border-bottom:1px solid var(--c-border)}thead th{color:var(--c-sub);font-weight:500}summary{cursor:pointer;color:var(--c-accent)}.limit{color:var(--c-sub)}ul{padding-left:20px}
</style>
