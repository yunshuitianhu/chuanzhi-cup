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
      borderColor: '#2a3947', textStyle: { color: '#e8eef5', fontSize: 15 },
      valueFormatter: (v) => (v === null || v === undefined ? '—' : Number(v).toFixed(2)),
    },
    legend: {
      top: 0, right: 0, itemWidth: 12, itemHeight: 8,
      textStyle: { color: '#94a7b8', fontSize: 14 },
    },
    xAxis: {
      type: 'time',
      axisLine: { lineStyle: { color: '#2a3947' } },
      axisLabel: { color: '#94a7b8', fontSize: 14, hideOverlap: true },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value',
      name: p.unit || '',
      nameTextStyle: { color: '#94a7b8', fontSize: 14 },
      axisLabel: { color: '#94a7b8', fontSize: 14 },
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
  <div class="charts-page">
  <header class="page-heading">
    <div class="page-copy">
      <span class="page-kicker">巡航记录 / 可视化研判</span>
      <h1>分析图表</h1>
      <p>沿时间和河道核对异常，从监测记录回到可追溯的证据。</p>
    </div>
    <div v-if="store.status.has_data" class="dataset-summary" aria-label="当前图表数据">
      <div><strong>{{ store.status.rows }}</strong><span>条监测记录</span></div>
      <span class="dataset-tag">目标 {{ store.status.target_water_class }} 类</span>
      <span v-if="store.status.data_identity?.label" class="dataset-identity">{{ store.status.data_identity.label }}</span>
    </div>
  </header>
  <ContextSourceFocus :kinds="['sample','time_range','video_frame']" @focus="acceptContext" />
  <div v-if="!store.status.has_data" class="empty">
    <div class="empty-ico" aria-hidden="true"><span></span><span></span><span></span><span></span></div>
    <h2>先载入一份巡航记录</h2>
    <p>请先在左侧上传并加载走航数据</p>
    <p class="empty-note">加载后可查看时间趋势、河道点位和传感器质量复核。</p>
  </div>

  <template v-else>
    <nav class="section-nav" aria-label="图表内容导航">
      <a href="#quality-review">质量复核</a><a href="#time-series">时间趋势</a><a href="#trace-map">溯源地图</a><a href="#pollution-playback">扩散演示</a><a href="#video-quality">视频核证</a>
    </nav>
    <details id="quality-review" class="quality-entry">
      <summary><strong>传感器质量复核</strong><span>查看质控标记与原始记录</span></summary>
      <div class="quality-details"><p class="caption">突刺、近恒值与采样异常需要复核；原始阈值预警保持。</p><DataQualityPanel /></div>
    </details>
    <Panel id="time-series" class="chart-section" title="时间序列趋势" subtitle="氨氮 / 溶解氧 / 浑浊度 / pH，黑点按严重等级着色">
      <template #actions>
        <button class="mini" :disabled="loading" @click="loadTimeSeries">刷新</button>
      </template>
      <p v-if="caption" class="caption">{{ caption }}</p>

      <div class="grid-2">
        <section v-for="panel in panels" :key="panel.key" class="trend-card" :aria-label="indicatorNames[panel.key]+'时间趋势'">
        <div class="trend-heading"><h3>{{ indicatorNames[panel.key] }}</h3><span>监测趋势</span></div>
        <EChart
          :option="panel.option" @range-selected="applyZoom(panel.key,$event)"
          :loading="loading"
          :height="260"
        />
        </section>
      </div>
      <div class="range-context">
        <div class="range-heading"><h3>圈定时间，精准追问</h3><span>引用当前监测时段</span></div>
        <p>拖动任一图表的缩放条选择时间范围，也可填写开始和结束时间；按监测表时间填写，未提供时区时不跨时区推算。</p>
        <div class="range-fields">
          <label>开始时间<input v-model="rangeStart" aria-label="追问范围开始时间" maxlength="80" :disabled="loading||store.loading||store.chatRunning" /></label>
          <label>结束时间<input v-model="rangeEnd" aria-label="追问范围结束时间" maxlength="80" :disabled="loading||store.loading||store.chatRunning" /></label>
          <label>关注指标<select v-model="rangeIndicator" aria-label="追问范围指标" :disabled="loading||store.loading||store.chatRunning"><option v-for="(label,key) in indicatorNames" :key="key" :value="key">{{label}}</option></select></label>
        </div>
        <AskContext :selector="rangeSelector" :disabled="loading" label="让AI分析这个时间范围" />
      </div>
    </Panel>

    <Panel id="trace-map" class="chart-section" title="交互式溯源地图" subtitle="严重程度渐变热力图 · 轨迹 · 可选聚类与 Top5 点位">
      <EvidenceFocus @focus="evidenceSampleId=$event" />
      <TraceMap :focus-sample-id="evidenceSampleId" />
    </Panel>

    <Panel id="pollution-playback" class="chart-section" title="污染扩散演示" subtitle="当前项目模拟结果 · 时间轴播放 · 沿程浓度与卫星地图同步">
      <PollutionPlayback />
    </Panel>
    <Panel id="video-quality" class="chart-section" title="视频-传感器交叉印证" subtitle="需要先上传巡航视频">
      <CrossValidation :focus-frame-id="focusFrameId" :focus-video-revision="focusVideoRevision" />
    </Panel>
  </template>
  </div>
</template>

<style scoped>
.charts-page { min-width:0; }
.page-heading { display:flex;align-items:flex-start;justify-content:space-between;gap:24px;padding:6px 0 24px; }
.page-copy { min-width:0; }
.page-kicker { color:var(--c-accent);font-size: 14px;letter-spacing:1.2px; }
.page-heading h1 { margin:6px 0 8px;font-size: 30px;line-height:1.4;font-weight:650;letter-spacing:.5px; }
.page-heading p { margin:0;color:var(--c-sub);font-size: 15px;line-height:1.8; }
.dataset-summary { display:flex;flex-wrap:wrap;align-items:center;justify-content:flex-end;gap:9px 12px;padding-top:4px;max-width:330px; }
.dataset-summary>div { display:flex;align-items:baseline;gap:7px; }
.dataset-summary strong { font-size: 29px;line-height:1.2;font-weight:600;font-variant-numeric:tabular-nums; }
.dataset-summary>div>span { color:var(--c-sub);font-size: 14px; }
.dataset-tag,.dataset-identity { font-size: 14px;line-height:1.5; }
.dataset-tag { color:var(--c-ink);border-left:1px solid var(--c-border);padding-left:12px; }
.dataset-identity { color:var(--c-sub);width:100%;text-align:right;overflow-wrap:anywhere; }
.section-nav { display:flex;flex-wrap:wrap;gap:5px;margin:0 0 20px;padding:5px;border:1px solid var(--c-border);border-radius:10px;background:var(--c-surface); }
.section-nav a { padding:7px 13px;border-radius:6px;color:var(--c-sub);font-size: 14px;text-decoration:none;transition:background var(--duration-fast),color var(--duration-fast); }
.section-nav a:hover,.section-nav a:focus-visible { background:var(--c-surface-2);color:var(--c-ink); }
.chart-section { scroll-margin-top:80px; }
.empty { text-align:center;padding:72px 24px;color:var(--c-sub);border:1px dashed var(--c-border);border-radius:var(--radius-md);background:var(--c-surface); }
.empty h2 { margin:22px 0 8px;font-size: 20px;color:var(--c-ink);font-weight:500; }
.empty p { margin:5px 0;font-size: 15px; }
.empty .empty-note { font-size: 14px; }
.empty-ico { display:flex;align-items:flex-end;justify-content:center;gap:7px;height:42px; }
.empty-ico span { width:7px;border-radius:3px;background:var(--c-accent);opacity:.65; }
.empty-ico span:nth-child(1){height:20px}.empty-ico span:nth-child(2){height:35px}.empty-ico span:nth-child(3){height:27px}.empty-ico span:nth-child(4){height:42px;opacity:1}
.caption { margin:0 0 16px;font-size: 14px;line-height:1.8;color:var(--c-sub); }
.range-context { padding:17px 18px;background:var(--c-surface-2);border:1px solid var(--c-border);border-radius:10px;margin-bottom:22px; }
.range-heading { display:flex;align-items:baseline;flex-wrap:wrap;gap:8px 16px;margin-bottom:7px; }
.range-heading h3 { margin:0;font-size: 16px;font-weight:600; }
.range-heading>span { font-size: 14px;color:var(--c-sub); }
.range-context p { font-size: 14px;line-height:1.8;color:var(--c-sub);margin:0 0 14px;max-width:75em; }
.range-fields { display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr) minmax(100px,.55fr);gap:12px;margin-bottom:14px; }
.range-fields label { display:flex;flex-direction:column;gap:6px;font-size: 14px;color:var(--c-sub);min-width:0; }
.range-fields input,.range-fields select { min-width:0;width:100%;font:inherit;font-size: 14px;color:var(--c-ink);background:var(--c-surface);border:1px solid var(--c-border);border-radius:7px;padding:9px 10px;min-height:38px; }
.grid-2 { display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:18px; }
.trend-card { min-width:0;border:1px solid var(--c-border);border-radius:9px;overflow:hidden;background:var(--c-bg); }
.trend-heading { display:flex;align-items:center;justify-content:space-between;gap:10px;padding:12px 14px 8px; }
.trend-heading h3 { margin:0;font-size: 15px;font-weight:600; }
.trend-heading span { color:var(--c-sub);font-size: 14px; }
.mini { background:var(--c-surface-2);border:1px solid var(--c-border);color:var(--c-sub);padding:6px 12px;min-height:32px;border-radius:7px;font-size: 14px; }
.mini:hover:not(:disabled) { border-color:var(--c-accent);color:var(--c-ink); }
.mini:disabled { opacity:.5; }
@media(max-width:1200px){.grid-2{grid-template-columns:minmax(0,1fr)}}
@media(max-width:700px){
  .page-heading{flex-direction:column;gap:16px;padding-bottom:20px}.page-heading h1{font-size: 26px}
  .dataset-summary{justify-content:flex-start;max-width:none;padding:0}.dataset-summary strong{font-size: 25px}
  .dataset-identity{width:auto;text-align:left}.section-nav{gap:2px}.section-nav a{padding:7px 9px;font-size: 14px}
  .range-fields{grid-template-columns:minmax(0,1fr)}.range-context{padding:14px}.range-heading{gap:5px 12px}
  .empty{padding:54px 16px}.grid-2{gap:12px}
}

.quality-entry{border:1px solid var(--c-border-soft);border-radius:var(--radius-sm);background:var(--c-surface);margin-bottom:18px;scroll-margin-top:84px}
.quality-entry>summary{padding:13px 16px;color:var(--c-sub);font-size: 14px;line-height:1.8}
.quality-entry>summary strong{font-weight:500;color:var(--c-ink);margin-right:18px}
.quality-entry>summary span{font-size: 14px}
.quality-details{padding:8px 18px 18px;border-top:1px solid var(--c-border-soft)}
.range-context{margin:20px 0 0}
@media(max-width:600px){.quality-entry>summary{padding:12px}.quality-entry>summary span{display:block;margin-left:15px}.quality-details{padding:10px 12px}}
</style>
