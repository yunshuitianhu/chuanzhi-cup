<script setup>
/**
 * 视频-传感器交叉印证结果展示。
 *
 * 对应 app.py 的 cross_validation.png 与 correlation_heatmap.png，
 * 改为 ECharts 热力图 + 一致性指标卡片。
 */
import { computed, ref, watch } from 'vue'
import { api } from '../api/client'
import { useSessionStore } from '../stores/session'
import EChart from './EChart.vue'
import { toastErr, toastOk } from '../composables/useToast'

const store = useSessionStore()
const offset = ref(0)
const windowSec = ref(30)
const result = ref(null)
const running = ref(false)

const hasVideo = computed(() => store.status.video_frames > 0)

async function run() {
  running.value = true
  try {
    result.value = await store.runCrossValidation({
      time_offset_sec: Number(offset.value) || 0,
      time_window_sec: Number(windowSec.value) || 30,
    })
    toastOk('交叉印证完成')
  } catch (e) {
    toastErr(`交叉印证失败：${e.message}`)
  } finally {
    running.value = false
  }
}

/** 相关系数热力图：视频指标 × 传感器指标。 */
const heatOption = computed(() => {
  const corr = result.value?.['相关系数矩阵'] || []
  if (!corr.length) return {}

  const vLabels = [...new Set(corr.map((c) => c.视频指标))]
  const sLabels = [...new Set(corr.map((c) => c.传感器指标))]
  const data = corr.map((c) => [
    sLabels.indexOf(c.传感器指标), vLabels.indexOf(c.视频指标), c.Pearson_r,
  ])

  return {
    grid: { left: 108, right: 76, top: 16, bottom: 46 },
    tooltip: {
      backgroundColor: 'rgba(20,30,40,.95)', borderColor: '#2a3947',
      textStyle: { color: '#e8eef5', fontSize: 12 },
      formatter: (p) => {
        const c = corr.find(
          (x) => x.视频指标 === vLabels[p.value[1]] && x.传感器指标 === sLabels[p.value[0]],
        )
        return c ? `${c.视频指标} × ${c.传感器指标}<br>Pearson r = ${c.Pearson_r}<br>`
          + `p = ${c.p_value}（${c.显著性}）<br>样本 ${c.样本数}` : ''
      },
    },
    xAxis: {
      type: 'category', data: sLabels, splitArea: { show: true },
      axisLabel: { color: '#94a7b8', fontSize: 11 },
      axisLine: { lineStyle: { color: '#2a3947' } },
    },
    yAxis: {
      type: 'category', data: vLabels, splitArea: { show: true },
      axisLabel: { color: '#94a7b8', fontSize: 11 },
      axisLine: { lineStyle: { color: '#2a3947' } },
    },
    visualMap: {
      min: -1, max: 1, calculable: true, orient: 'vertical', right: 8, top: 'center',
      textStyle: { color: '#94a7b8', fontSize: 11 },
      inRange: { color: ['#2ea3f2', '#17212b', '#ff4d6d'] },
    },
    series: [{
      type: 'heatmap', data,
      label: {
        show: true, fontSize: 11,
        formatter: (p) => Number(p.value[2]).toFixed(2),
        color: '#e8eef5',
      },
      emphasis: { itemStyle: { shadowBlur: 8, shadowColor: 'rgba(0,0,0,.5)' } },
    }],
  }
})

/**
 * ⚠️ 键名必须与 `analysis.cross_validate_video_with_monitoring` 的
 * **实际产出**一致，全部是中文键。
 *
 * 本组件最初按英文键（alignment / agreement / correlations / findings）
 * 读取，后端一个都没有 —— `.get()` 取不到时静默落到 `?? '—'` 兜底，
 * 于是四个指标卡全是空白横杠，而接口返回的是 200、数据也完全正常。
 * 这正是项目里反复出现的那类缺陷，只不过这次发生在新前端上。
 * 改动前请用 `tests/前端字段名检查.py` 校验。
 */
const alignment = computed(() => result.value?.['时间对齐'] || {})
const agreement = computed(() => result.value?.['黑臭判定一致性'] || {})
const findings = computed(() => result.value?.['关键发现'] || [])

watch(() => store.status.video_frames, (n) => { if (!n) result.value = null })
</script>

<template>
  <div v-if="!hasVideo" class="tip">
    请先在左侧上传巡航视频并加载，再进行交叉印证。
  </div>

  <template v-else>
    <div class="controls">
      <label>
        时间偏移 (秒)
        <input v-model.number="offset" type="number" step="10" />
      </label>
      <label>
        匹配窗口 (秒)
        <input v-model.number="windowSec" type="number" step="10" />
      </label>
      <button class="run" :disabled="running" @click="run">
        {{ running ? '计算中…' : '运行交叉印证' }}
      </button>
      <span class="hint">视频已提取 {{ store.status.video_frames }} 帧</span>
    </div>

    <template v-if="result">
      <div class="metrics">
        <div class="metric">
          <span class="m-label">匹配成功帧</span>
          <span class="m-val">{{ alignment['匹配成功帧数'] ?? '—' }}</span>
          <span class="m-sub">/ {{ alignment['总视频帧数'] ?? '—' }}</span>
        </div>
        <div class="metric">
          <span class="m-label">黑臭判定一致率</span>
          <span class="m-val">{{ agreement['一致率_%'] ?? '—' }}<small>%</small></span>
          <span class="m-sub">{{ agreement['双方一致_黑臭'] ?? 0 }} 帧双方判黑</span>
        </div>
        <div class="metric">
          <span class="m-label">视频判黑准确率</span>
          <span class="m-val">{{ agreement['视频判黑准确率_%'] ?? '—' }}<small>%</small></span>
        </div>
        <div class="metric">
          <span class="m-label">视频判黑召回率</span>
          <span class="m-val">{{ agreement['视频判黑召回率_%'] ?? '—' }}<small>%</small></span>
        </div>
      </div>

      <p v-if="agreement['解读']" class="interp">{{ agreement['解读'] }}</p>

      <EChart v-if="heatOption.series" :option="heatOption" :height="300" />

      <ul v-if="findings.length" class="findings">
        <li v-for="(f, i) in findings" :key="i">{{ f }}</li>
      </ul>
    </template>
  </template>
</template>

<style scoped>
.tip { padding: 30px; text-align: center; color: var(--c-sub); font-size: 13px; }

.controls { display: flex; align-items: flex-end; gap: 14px; flex-wrap: wrap; margin-bottom: 16px; }
.controls label { display: flex; flex-direction: column; gap: 5px; font-size: 12px; color: var(--c-sub); }
.controls input {
  background: var(--c-surface-2); border: 1px solid var(--c-border); color: var(--c-ink);
  border-radius: 6px; padding: 6px 10px; width: 120px; font-family: inherit;
}
.run {
  background: var(--c-accent); color: #06121c; border: none;
  padding: 8px 18px; border-radius: 6px; font-weight: 600;
}
.run:disabled { opacity: .5; cursor: not-allowed; }
.hint { font-size: 12px; color: var(--c-sub); }

.metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 12px; }
@media (max-width: 900px) { .metrics { grid-template-columns: repeat(2, 1fr); } }
.metric {
  background: var(--c-surface-2); border: 1px solid var(--c-border);
  border-radius: 8px; padding: 10px 13px; display: flex; flex-direction: column; gap: 2px;
}
.m-label { font-size: 11px; color: var(--c-sub); }
.m-val { font-size: 22px; font-weight: 700; color: var(--c-accent); }
.m-val small { font-size: 12px; margin-left: 1px; }
.m-sub { font-size: 11px; color: var(--c-sub); }

.interp {
  font-size: 12px; color: var(--c-sub); margin: 0 0 12px;
  padding: 8px 12px; background: var(--c-surface-2); border-radius: 6px;
  border-left: 2px solid var(--c-accent);
}
.findings { margin: 12px 0 0; padding-left: 18px; font-size: 12px; color: var(--c-sub); }
.findings li { margin: 3px 0; }
</style>
