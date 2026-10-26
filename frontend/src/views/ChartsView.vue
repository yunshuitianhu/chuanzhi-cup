<script setup>
/**
 * 分析图表页 —— 对应 app.py 的 tab2。
 *
 * 与原版的本质区别：原版是把 matplotlib 生成的 PNG 贴上来，
 * 这里全部由 ECharts 在前端渲染 —— 可缩放、可悬浮读数、可切换图例。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../api/client'
import { useSessionStore } from '../stores/session'
import EChart from '../components/EChart.vue'
import Panel from '../components/Panel.vue'
import TraceMap from '../components/TraceMap.vue'
import EvidenceFocus from '../components/EvidenceFocus.vue'
import CrossValidation from '../components/CrossValidation.vue'
import PollutionPlayback from '../components/PollutionPlayback.vue'
import DataQualityPanel from '../components/DataQualityPanel.vue'
import { toastErr } from '../composables/useToast'
import AskContext from '../components/AskContext.vue'
import ContextSourceFocus from '../components/ContextSourceFocus.vue'
import {timeRangeFromZoom} from '../utils/questionContextSelection'

const store = useSessionStore()
const ts = ref(null)          // time-series 数据
const loading = ref(false)
const evidenceSampleId=ref(null)
const focusFrameId=ref(null),focusVideoRevision=ref(null)
const rangeStart=ref(''),rangeEnd=ref(''),rangeIndicator=ref('ammonia_nitrogen'),rangeFromContext=ref(false)
const rangeSelector=computed(()=>rangeStart.value&&rangeEnd.value
  ? {kind:'time_range',start:rangeStart.value,end:rangeEnd.value,indicator:rangeIndicator.value}:null)
const indicatorNames={ammonia_nitrogen:'氨氮',dissolved_oxygen:'溶解氧',turbidity:'浊度',ph:'pH'}
function acceptContext(card){
  evidenceSampleId.value=null;focusFrameId.value=null;focusVideoRevision.value=null
  if(rangeFromContext.value){rangeStart.value='';rangeEnd.value='';rangeFromContext.value=false}
  if(!card)return
  if(card.kind==='sample')evidenceSampleId.value=card.selection.sample_id
  if(card.kind==='video_frame'){
    focusFrameId.value=card.selection.frame_id
    focusVideoRevision.value=card.selection.video_revision
  }
  if(card.kind==='time_range'){
    rangeStart.value=card.selection.start;rangeEnd.value=card.selection.end
    rangeIndicator.value=card.selection.indicator;rangeFromContext.value=true
  }
}
function applyZoom(indicator,event){
  const range=timeRangeFromZoom(ts.value?.time,event)
  if(!range)return
  rangeStart.value=range.start;rangeEnd.value=range.end;rangeIndicator.value=indicator;rangeFromContext.value=false
}
let requestVersion = 0

async function loadTimeSeries() {
  const version = ++requestVersion
  ts.value = null
  if (!store.status.has_data) { loading.value = false; return }
  loading.value = true
  try {
    const result = await api.timeSeries()
    if (version === requestVersion) {
      ts.value = result
      if(!rangeFromContext.value){rangeStart.value=result.time?.[0]||'';rangeEnd.value=result.time?.at(-1)||''}
    }
  } catch (e) {
    if (version === requestVersion) toastErr(`图表数据加载失败：${e.message}`)
  } finally {
    if (version === requestVersion) loading.value = false
  }
}

onMounted(loadTimeSeries)
onBeforeUnmount(() => { requestVersion++ })
// 数据或黑点变化后重新取图（黑点散点依赖检测结果）
watch(() => [store.status.analysis_revision, store.status.rows,
  store.status.black_spot_count, store.status.target_water_class, store.status.results_revision], loadTimeSeries)

/** 把某一面板的数据 + 黑点散点拼成 ECharts option。 */
function panelOption(key) {
  const p = ts.value?.panels?.find((x) => x.key === key)
  if (!p) return { series: [] }

  const spots = (ts.value?.black_spots || [])
    .map((s) => [s.t, s[key === 'ammonia_nitrogen' ? 'ammonia'
      : key === 'dissolved_oxygen' ? 'do'
      : key === 'turbidity' ? 'turbidity' : 'ph'], s.severity_label, s.color])
    .filter((r) => r[1] !== null && r[1] !== undefined)

  // 按严重等级分组，图例才能分开勾选
  const groups = {}
  for (const [t, v, label, color] of spots) {
    const g = groups[label] || (groups[label] = { color, data: [] })
    g.data.push([t, v])
  }

  const series = [{
    name: p.label,
    type: 'line',
    showSymbol: false,
    lineStyle: { width: 1, color: p.color },
    itemStyle: { color: p.color },
    data: ts.value.time.map((t, i) => [t, p.values[i]]),
    // 数据量大，采样能显著提速；lttb 保留极值形态
    sampling: 'lttb',
    large: true,
  }]

  if (p.limit !== null && p.limit !== undefined) {
    series.push({
      name: `${ts.value.target_water_class}类${p.limit_direction === 'minimum' ? '下限' : '上限'} ${p.limit}`,
      type: 'line',
      showSymbol: false,
      lineStyle: { color: '#ff4d6d', type: 'dashed', width: 1 },
      itemStyle: { color: '#ff4d6d' },
      // 参考线参与坐标轴范围计算，避免所有实测值超标时限值线落在画布外。
      data: ts.value.time.length
        ? [[ts.value.time[0], p.limit], [ts.value.time.at(-1), p.limit]] : [],
    })
  }

  for (const [label, g] of Object.entries(groups)) {
    series.push({
      name: label,
      type: 'scatter',
      symbolSize: 5,
      itemStyle: { color: g.color },
      data: g.data,
    })
  }

  return {
    grid: { left: 52, right: 20, top: 30, bottom: 54 },
    dataZoom:[{type:'inside',xAxisIndex:0,filterMode:'none'},{type:'slider',xAxisIndex:0,bottom:3,height:14,filterMode:'none'}],
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross', lineStyle: { color: '#3a5163' } },
      backgroundColor: 'rgba(20,30,40,.95)',
      borderColor: '#2a3947', textStyle: { color: '#e8eef5', fontSize: 12 },
      valueFormatter: (v) => (v === null || v === undefined ? '—' : Number(v).toFixed(2)),
    },
    legend: {
      top: 0, right: 0, itemWidth: 12, itemHeight: 8,
      textStyle: { color: '#94a7b8', fontSize: 11 },
    },
    xAxis: {
      type: 'time',
      axisLine: { lineStyle: { color: '#2a3947' } },
      axisLabel: { color: '#94a7b8', fontSize: 10, hideOverlap: true },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value',
      name: p.unit || '',
      nameTextStyle: { color: '#94a7b8', fontSize: 10 },
      axisLabel: { color: '#94a7b8', fontSize: 10 },
      splitLine: { lineStyle: { color: '#1e2a36' } },
      scale: true,
    },
    series,
  }
}

const PANEL_KEYS = ['ammonia_nitrogen', 'dissolved_oxygen', 'turbidity', 'ph']
// Stable options: selection/UI changes must not rebuild all four time-series arrays.
const panels = computed(() => PANEL_KEYS.map(key => ({key, option:panelOption(key)})))
const caption = computed(() => store.status.has_data
  ? `监测时段 ${store.status.time_range?.[0]?.slice(5, 16) || ''} ~ ${store.status.time_range?.[1]?.slice(11, 16) || ''}，`
    + `共 ${store.status.rows} 条记录。散点为检出黑臭的点位，按等级着色。`
  : '')
</script>

<template>
  <ContextSourceFocus :kinds="['sample','time_range','video_frame']" @focus="acceptContext" />
  <div v-if="!store.status.has_data" class="empty">
    <div class="empty-ico">📊</div>
    <p>请先在左侧上传并加载走航数据</p>
  </div>

  <template v-else>
    <Panel title="传感器质量复核" subtitle="突刺 · 近恒值 · 采样异常 · 原始阈值预警保持">
      <DataQualityPanel />
    </Panel>
    <Panel id="time-series" style="scroll-margin-top:80px" title="时间序列趋势" subtitle="氨氮 / 溶解氧 / 浑浊度 / pH，黑点按严重等级着色">
      <template #actions>
        <button class="mini" :disabled="loading" @click="loadTimeSeries">刷新</button>
      </template>
      <p v-if="caption" class="caption">{{ caption }}</p>

      <div class="range-context">
        <p>拖动任一图表的缩放条选择时间范围，也可填写开始和结束时间；按监测表时间填写，未提供时区时不跨时区推算。</p>
        <div class="range-fields">
          <label>开始时间<input v-model="rangeStart" aria-label="追问范围开始时间" maxlength="80" :disabled="loading||store.loading||store.chatRunning" /></label>
          <label>结束时间<input v-model="rangeEnd" aria-label="追问范围结束时间" maxlength="80" :disabled="loading||store.loading||store.chatRunning" /></label>
          <label>关注指标<select v-model="rangeIndicator" aria-label="追问范围指标" :disabled="loading||store.loading||store.chatRunning"><option v-for="(label,key) in indicatorNames" :key="key" :value="key">{{label}}</option></select></label>
        </div>
        <AskContext :selector="rangeSelector" :disabled="loading" label="让AI分析这个时间范围" />
      </div>
      <div class="grid-2">
        <EChart
          v-for="panel in panels"
          :key="panel.key"
          :option="panel.option" @range-selected="applyZoom(panel.key,$event)"
          :loading="loading"
          :height="260"
        />
      </div>
    </Panel>

    <Panel id="trace-map" style="scroll-margin-top:80px" title="交互式溯源地图" subtitle="严重程度渐变热力图 · 轨迹 · 可选聚类与 Top5 点位">
      <EvidenceFocus @focus="evidenceSampleId=$event" />
      <TraceMap :focus-sample-id="evidenceSampleId" />
    </Panel>

    <Panel title="污染扩散演示" subtitle="当前项目模拟结果 · 时间轴播放 · 沿程浓度与卫星地图同步">
      <PollutionPlayback />
    </Panel>
    <Panel id="video-quality" style="scroll-margin-top:80px" title="视频-传感器交叉印证" subtitle="需要先上传巡航视频">
      <CrossValidation :focus-frame-id="focusFrameId" :focus-video-revision="focusVideoRevision" />
    </Panel>
  </template>
</template>

<style scoped>
.empty { text-align: center; padding: 80px 20px; color: var(--c-sub); }
.empty-ico { font-size: 40px; margin-bottom: 12px; }

.caption { margin: 0 0 12px; font-size: 12px; color: var(--c-sub); }

.range-context{padding:12px 14px;background:var(--c-surface-2);border:1px solid var(--c-border);border-radius:8px;margin-bottom:16px}.range-context p{font-size:12px;color:var(--c-sub);margin:0 0 10px}.range-fields{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr) minmax(90px,.5fr);gap:10px}.range-fields label{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--c-sub)}.range-fields input,.range-fields select{min-width:0;width:100%;font:inherit;color:var(--c-ink);background:var(--c-surface);border:1px solid var(--c-border);border-radius:6px;padding:8px}
@media(max-width:700px){.range-fields{grid-template-columns:1fr}.range-context{padding:10px}}
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
@media (max-width: 1200px) { .grid-2 { grid-template-columns: 1fr; } }

.mini {
  background: transparent; border: 1px solid var(--c-border); color: var(--c-sub);
  padding: 4px 12px; border-radius: 6px; font-size: 12px;
}
.mini:hover:not(:disabled) { border-color: var(--c-accent); color: var(--c-ink); }
.mini:disabled { opacity: .5; }
</style>
