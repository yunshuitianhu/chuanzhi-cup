<script setup>
/**
 * 黑臭检测结果展示 —— 对应 app.py 的 _display_black_spot_result。
 *
 * 聚类详情直接按后端返回的字段渲染，**不在前端硬编码字段名**。
 * 原 app.py 因为读了一组后端不存在的键（聚类ID / 严重程度 / 点位数量），
 * 导致该面板三个值恒为空白且无任何报错。用"按实际字段渲染"的方式
 * 从结构上避免同类问题。
 */
import { computed } from 'vue'
import EChart from './EChart.vue'

const props = defineProps({ data: { type: Object, required: true } })

const res = computed(() => props.data?.result || {})
const clusters = computed(() => res.value['聚类详情'] || [])
const dist = computed(() => res.value['黑臭等级分布'] || {})

const distOption = computed(() => {
  const d = dist.value
  const COLOR = { 轻度黑臭: '#FFA500', 重度黑臭: '#8B0000', 无黑臭: '#2E8B57' }
  return {
    grid: { left: 72, right: 40, top: 16, bottom: 24 },
    tooltip: {
      backgroundColor: 'rgba(20,30,40,.95)', borderColor: '#2a3947',
      textStyle: { color: '#e8eef5' },
    },
    xAxis: {
      type: 'value',
      axisLabel: { color: '#94a7b8' },
      splitLine: { lineStyle: { color: '#1e2a36' } },
    },
    yAxis: {
      type: 'category', data: Object.keys(d),
      axisLabel: { color: '#94a7b8' },
      axisLine: { lineStyle: { color: '#2a3947' } },
    },
    series: [{
      type: 'bar', barWidth: '46%',
      data: Object.entries(d).map(([k, v]) => ({
        value: v, itemStyle: { color: COLOR[k] || '#2ea3f2' },
      })),
      label: { show: true, position: 'right', color: '#e8eef5', fontSize: 11 },
    }],
  }
})

const METRICS = [
  ['黑点总数', '黑点总数'],
  ['聚类热点数', '聚类热点数'],
  ['氨氮峰值(mg/L)', '氨氮峰值 mg/L'],
  ['溶解氧最低(mg/L)', '溶解氧最低 mg/L'],
  ['增强评分最高', '增强评分最高'],
  ['非黑臭指数异常点数', '指数线索（非黑臭）'],
  ['有效判定点数', '有效筛查点'],
  ['未判定点数', '缺测未判定'],
]
</script>

<template>
  <div class="result">
    <div class="metrics">
      <div v-for="[key, label] in METRICS" :key="key" class="metric">
        <div class="m-k">{{ label }}</div>
        <div class="m-v">{{ res[key] ?? '—' }}</div>
      </div>
    </div>

    <p v-if="res['提示']" class="std">{{res['提示']}}</p>

    <p v-if="res['判定标准']" class="std">
      判定标准：{{ res['判定标准']['污染指数'] }} ｜ {{ res['判定标准']['黑臭判定'] }}
    </p>
    <p v-if="res['筛查口径']" class="std">{{res['筛查口径']}}</p>
    <p v-if="res['聚类提示']" class="std warn">{{res['聚类提示']}}</p>
    <p v-if="res['缺失指标'] && res['缺失指标'].length" class="std warn">
      缺失指标：{{ res['缺失指标'].join('、') }} ——
      黑臭判定仅基于氨氮与溶解氧两项，结果存在局限，此处如实标注。
    </p>

    <EChart v-if="Object.keys(dist).length" :option="distOption" :height="140" />

    <details v-if="clusters.length" class="clusters">
      <summary>聚类详情（{{ clusters.length }} 个热点）</summary>
      <div v-for="c in clusters" :key="c.cluster_id" class="cluster">
        <div class="c-head">
          <b>热点 {{ c.cluster_id }}</b>
          <span class="tag" :class="c['黑臭等级'] === '重度黑臭' ? 'bad' : 'mid'">
            {{ c['黑臭等级'] }}
          </span>
          <span class="c-sub">{{ c['内梅罗等级'] }}</span>
        </div>
        <div class="c-body">
          <span>点位 {{ c.count }} 个</span>
          <span>跨度 {{ c['空间跨度_m'] }} m</span>
          <span v-if="c['首末观测跨度_min'] != null">首末观测跨度 {{ c['首末观测跨度_min'] }} min</span>
          <span v-else>观测跨度待重新分析</span>
          <span v-if="c['沿航迹指数变化']" :title="c['趋势解释']">沿航迹指数 {{ c['沿航迹指数变化'] }}</span>
          <span v-else-if="c['趋势']">历史趋势待重新分析</span>
        </div>
        <div v-if="c['指标统计']" class="c-stat">
          <span v-for="(v, k) in c['指标统计']" :key="k">{{ k }} {{ v }}</span>
        </div>
        <div v-if="c['主要触发指标']" class="c-trig">
          主要触发：{{
            Array.isArray(c['主要触发指标'])
              ? c['主要触发指标'].join('、')
              : c['主要触发指标']
          }}
        </div>
      </div>
    </details>
  </div>
</template>

<style scoped>
.result { margin-top: 16px; }
.metrics {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 8px; margin-bottom: 12px;
}
.metric {
  background: var(--c-surface-2); border: 1px solid var(--c-border);
  border-radius: 8px; padding: 9px 12px;
}
.m-k { font-size: 11px; color: var(--c-sub); }
.m-v { font-size: 19px; font-weight: 700; color: var(--c-accent); margin-top: 2px; }

.std { font-size: 12px; color: var(--c-sub); margin: 4px 0; }
.std.warn { color: #e8c98a; }

.clusters { margin-top: 12px; font-size: 12px; }
.clusters summary { cursor: pointer; color: var(--c-sub); padding: 6px 0; }
.cluster {
  border: 1px solid var(--c-border); border-radius: 8px;
  padding: 10px 13px; margin-top: 8px; background: var(--c-surface-2);
}
.c-head { display: flex; align-items: center; gap: 8px; margin-bottom: 5px; }
.tag { font-size: 11px; padding: 1px 8px; border-radius: 20px; }
.tag.bad { background: #4a1018; color: #ff8fa3; }
.tag.mid { background: #4a3a10; color: #ffd479; }
.c-sub { color: var(--c-sub); font-size: 11px; }
.c-body, .c-stat { display: flex; flex-wrap: wrap; gap: 14px; color: var(--c-sub); font-size: 11px; }
.c-trig { margin-top: 4px; color: var(--c-sub); font-size: 11px; }
</style>
