<script setup>
import report from '../data/refinedInverseValidation.json'
const percent = value => `${(value * 100).toFixed(2)}%`
</script>

<template>
  <div class="inverse-tech">
    <p>在给定排放时段、流量、边界和反应参数的前提下，搜索候选源位置与负荷。
      拟合使用细分水质网格，再用加倍网格复核；最下游测站的观测留出，不参加拟合。</p>
    <p class="scope"><b>离线受控验证 · {{ report.date }}</b> · 与当前上传数据无关 · 真实数据反演尚未开放</p>
    <div class="table-scroll" role="region" tabindex="0" aria-label="受控单源反演跨河段验证结果">
      <table>
        <caption>独立解析参照 · 100 个水动力断面 → 397 节点拟合 → 793 节点复核</caption>
        <thead><tr><th scope="col">河段长度</th><th scope="col">负荷误差</th><th scope="col">留出浓度误差</th><th scope="col">源候选恢复</th></tr></thead>
        <tbody><tr v-for="row in report.crossRiver" :key="row.length_m">
          <th scope="row">{{ row.length_m / 1000 }} km</th>
          <td>{{ percent(row.load_relative_error) }}</td>
          <td>{{ percent(row.holdout_truth_nrmse) }}</td>
          <td>{{ row.source_recovered ? '通过' : '未通过' }}</td>
        </tr></tbody>
      </table>
    </div>
    <p class="note">表中为固定种子、2.5% 峰值噪声的三组理想恒定流算例。留出误差按真实污染增量峰值归一化；不是现场准确率，也不是任意位置的定位精度。</p>
    <details>
      <summary>哪些情况下拒绝给出估计</summary>
      <p>信号不足、残差过大、多个候选近似等价、负荷触界、相差 20% 的负荷无法区分、网格加密变化过大、留出观测不匹配，或缺少增益界限与校准依据，均不输出已接受的估计。超出计算预算会明确停止。</p>
      <p>完成 {{ report.noiseCases }} 组固定种子噪声检查（2.5% / 10% / 25%），以及 {{ report.declineCases }} 组偏差或信息不足检查。高噪声允许拒绝；检查通过不表示每组都能恢复来源。</p>
    </details>
    <p class="limitation"><b>仍存在的盲点：</b>{{ report.knownBlindSpot }} 本轮保留了该负例；正确复现盲点不计作成功溯源。多源、未知排放时段及真实河道归因仍需后续验证。</p>
  </div>
</template>

<style scoped>
.inverse-tech { font-size: 12px; color: var(--c-sub); line-height: 1.85; }
p { margin: 8px 0; max-width: 850px; }
.scope { color: var(--c-ink); font-size: 11px; }
.table-scroll { overflow-x: auto; border: 1px solid var(--c-border); border-radius: 8px; margin-top: 12px; }
table { width: 100%; min-width: 470px; border-collapse: collapse; text-align: left; font-variant-numeric: tabular-nums; }
caption { padding: 9px 12px; background: var(--c-surface-2); text-align: left; font-size: 11px; }
th, td { padding: 9px 12px; border-top: 1px solid var(--c-border); }
th { font-weight: 500; color: var(--c-ink); }
.note, details { font-size: 11px; }
details { margin-top: 12px; }
summary { cursor: pointer; color: var(--c-ink); }
.limitation { margin-top: 12px; border-left: 3px solid var(--c-accent); padding: 4px 12px; background: var(--c-surface-2); font-size: 11px; }
.table-scroll:focus-visible { outline: 2px solid var(--c-accent); outline-offset: 3px; }
</style>
