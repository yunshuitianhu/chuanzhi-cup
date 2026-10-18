<script setup>
/**
 * 交互式溯源地图（Leaflet）。
 *
 * 对应 app.py 中 folium 生成的 trace_map.html，改为前端直接渲染：
 *   - 走航轨迹（折线）
 *   - 聚类热点（虚线范围圆 + 实心中心，颜色按内梅罗等级）
 *   - Top-5 严重点位（编号标记）
 *   - 污染点位热力图
 *
 * 高德官方静态底图经后端取图；显示坐标转GCJ-02，原始数据保持WGS-84。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { severityLayer } from '../utils/severityLayer'
import { amapStaticLayer } from '../utils/amapStaticLayer'
import { displayTrace, wgsToGcj, gcjToWgs } from '../utils/mapCoordinates'
import { api } from '../api/client'
import { useSessionStore } from '../stores/session'
import { toastErr } from '../composables/useToast'
import { nearestSamples, sampleMetrics, numberText } from '../utils/mapInteraction'
import { simulationColor } from '../utils/playback'

const props = defineProps({simulation:{type:Object,default:null}, frameIndex:{type:Number,default:0}})

const store = useSessionStore()
const el = ref(null)
const data = ref(null)
const loading = ref(false)
const showHeat = ref(true), showClusters = ref(false), showTop5 = ref(true)
const showTrack = ref(false), heatRadius = ref(26), heatOpacity = ref(.94)
const wrapper = ref(null), fullscreen = ref(false)
let map = null
let layerGroup = null
let baseLayer = null, resizeObserver = null, disposed = false, loadVersion = 0, sourceVersion = 0
const provider = ref(store.config.keys.starcloud ? 'starcloud' : store.config.keys.amap ? 'amap' : 'esri')
const baseState = ref('loading'), baseMessage = ref('正在加载底图…'), fallbackReason = ref('')
const tried = new Set()
const selectedId = ref(null), nearbyIds = ref([])
const selectedSample = computed(() => data.value?.samples?.find(s=>s.id === selectedId.value))
const sampleRows = computed(() => sampleMetrics.map(([key,label,unit])=>({key,label,unit,
  value:selectedSample.value?.values?.[key], interpolated:selectedSample.value?.interpolated?.[key], source:selectedSample.value?.sources?.[key]})))
const sourceNames = {starcloud:'星图卫星影像', amap:'高德街道图', esri:'Esri 卫星图', osm:'OSM 街道图'}
const sourceMaxZoom = source => source === 'amap' ? 17 : source === 'starcloud' ? 18 : 19

async function refreshDetection() {
  try {await store.runBlackSpots()} catch(e) {toastErr(`检测更新失败：${e.message}`)}
}

function updateFullscreen() { fullscreen.value = document.fullscreenElement === wrapper.value }
async function toggleFullscreen() {
  try {
    if (fullscreen.value) await document.exitFullscreen()
    else await wrapper.value?.requestFullscreen()
  } catch { toastErr('当前窗口不支持全屏，请使用浏览器全屏模式。') }
}

function selectSource(source, automatic = false) {
  if (!map) return
  if (!automatic) { tried.clear(); fallbackReason.value = '' }
  const previous = provider.value
  const c = map.getCenter()
  let center = [c.lat, c.lng]
  if (previous === 'amap') center = gcjToWgs(center)
  if (source === 'amap') center = wgsToGcj(center)
  const version = ++sourceVersion
  baseLayer?.remove()
  provider.value = source
  tried.add(source)
  map.setMaxZoom(sourceMaxZoom(source))
  map.setView(center, Math.min(map.getZoom(), sourceMaxZoom(source)), {animate:false})
  baseState.value = 'loading'; baseMessage.value = `正在加载${sourceNames[source]}…`
  let successes = 0, failures = 0, fallbackQueued = false
  const report = (state, message) => {
    if (disposed || version !== sourceVersion) return
    baseState.value = state; baseMessage.value = message
    if (state !== 'error' || fallbackQueued) return
    const next = ['starcloud','amap','esri','osm'].find(s => !tried.has(s)
      && (s !== 'amap' || store.config.keys.amap) && (s !== 'starcloud' || store.config.keys.starcloud))
    if (next) {
      fallbackQueued = true
      fallbackReason.value = `${sourceNames[source]}加载失败：${message}；已尝试备用底图。`
      queueMicrotask(() => { if (!disposed && version === sourceVersion) selectSource(next, true) })
    }
  }
  if (source === 'amap') baseLayer = amapStaticLayer(api.amapStaticMap, report)
  else {
    const osm = source === 'osm', satellite = source === 'starcloud'
    baseLayer = L.tileLayer(satellite ? '/api/maps/starcloud/{z}/{x}/{y}' : osm
      ? 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
      : 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
      maxZoom:sourceMaxZoom(source), keepBuffer:1,
      attribution:satellite ? '&copy; <a href="https://www.geovisearth.com/">星图地球</a>'
        : osm ? '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>' : 'Esri',
    })
    baseLayer.on('tileload', () => { successes++; report('ready', `${sourceNames[source]}已加载`) })
    baseLayer.on('tileerror', () => {
      failures++
      if (failures >= 3 && !successes) report('error', '底图服务不可达或拒绝访问')
      else if (successes) report('partial', '部分底图未加载，可重试或切换底图')
    })
    baseLayer.on('load', () => { if (failures && !successes) report('error', '底图服务不可达或拒绝访问') })
  }
  baseLayer.addTo(map)
  render()
}

function initMap() {
  if (map || !el.value) return
  map = L.map(el.value, { zoomControl: true, attributionControl: true, minZoom:1, worldCopyJump:true })
  layerGroup = L.layerGroup().addTo(map)
  map.setView([30, 105], 3)   // 中性初始视角；有数据后 fitBounds
  map.on('click', onMapClick)
  selectSource(provider.value)
  resizeObserver = new ResizeObserver(() => map?.invalidateSize())
  resizeObserver.observe(el.value)
}

function onMapClick(event) {
  if (props.simulation || !data.value) return
  const d = displayTrace(data.value, provider.value === 'amap')
  nearbyIds.value = nearestSamples(d.samples, event.containerPoint || map.latLngToContainerPoint(event.latlng),
    coord=>map.latLngToContainerPoint(coord), Math.max(heatRadius.value,14))
  selectedId.value = nearbyIds.value[0] ?? null
}

function renderSimulation(fit) {
  const sim = props.simulation
  const coordinates = sim.mapping.positions.map(p=>provider.value === 'amap' ? wgsToGcj(p) : p)
  const values = sim.concentrations[props.frameIndex]
  if (!coordinates.length || !values) return
  for (let i=0;i<coordinates.length;i++) {
    const color=simulationColor(values[i],sim.color_scale)
    if (i) L.polyline([coordinates[i-1],coordinates[i]], {color,weight:10,opacity:.85,interactive:false}).addTo(layerGroup)
    L.circleMarker(coordinates[i],{radius:5,color:'#ffffff',weight:1,fillColor:color,fillOpacity:1})
      .addTo(layerGroup).bindPopup(`模拟断面 · 桩号 ${numberText(sim.chainage[i],1)} m<br>`
        + `T+${numberText(sim.times[props.frameIndex])} h<br>浓度 ${numberText(values[i])} mg/L`)
  }
  const source=sim.source.source_effective_chainage_m ?? sim.source.chainage_m
  if (source != null) {
    let nearest=0
    for(let i=1;i<sim.chainage.length;i++) if(Math.abs(sim.chainage[i]-source)<Math.abs(sim.chainage[nearest]-source))nearest=i
    L.circleMarker(coordinates[nearest],{radius:10,color:'#fff',weight:3,fillColor:'#222',fillOpacity:.8})
      .addTo(layerGroup).bindPopup(`情景排放点 · 实际计算桩号 ${numberText(sim.chainage[nearest],1)} m<br>`
        + `T+${numberText(sim.times[props.frameIndex])} h · 浓度 ${numberText(values[nearest])} mg/L`)
  }
  if (fit) map.fitBounds(L.latLngBounds(coordinates),{padding:[35,35],maxZoom:17})
}

function render(fit = false) {
  if (!map || !layerGroup) return
  layerGroup.clearLayers()
  if (props.simulation) { renderSimulation(fit); return }
  const d = displayTrace(data.value, provider.value === 'amap')
  if (!d) return

  // 轨迹
  if (d.track?.length) {
    const line = L.polyline(d.track, { color: '#a4dcff', weight: 1, opacity: .6 })
    if (showTrack.value) {
      line.addTo(layerGroup)
      line.bindPopup(`走航轨迹 · ${d.track.length} 个点`)
    }
    if (fit) map.fitBounds(line.getBounds(), { padding: [24, 24], maxZoom:17 })
  }

  // 邻域平均严重程度；密集采样不累加成更严重的颜色。
  if (showHeat.value && d.heat?.length) {
    severityLayer(d.heat, {radius:heatRadius.value, opacity:heatOpacity.value}).addTo(layerGroup)
  }

  // 聚类热点
  for (const c of (showClusters.value ? d.clusters || [] : [])) {
    L.circle(c.center, {
      radius: c.radius_m, color: c.color, weight: 1.5,
      fillOpacity: .06, dashArray: '5,5',
    }).addTo(layerGroup)

    L.circleMarker(c.center, {
      radius: Math.max(6, Math.min(16, Math.sqrt(c.count) * 1.6)),
      color: c.color, weight: 2, fillColor: c.color, fillOpacity: .55,
    }).addTo(layerGroup).bindPopup(
      `<b>热点 ${c.id} — ${c.grade}</b><br>`
      + `采样点：${c.count} 个<br>`
      + `时空增强评分均值：${c.avg_score}<br>`
      + `聚类显示半径：${Math.round(c.radius_m)} m<br>`
      + `时间：${c.time_range[0].slice(11, 19)} ~ ${c.time_range[1].slice(11, 19)}<br>`
      + `氨氮均值：${c.ammonia_mean ?? '—'} mg/L<br>`
      + `DO 均值：${c.do_mean ?? '—'} mg/L`,
    )
  }

  // Top-5 严重点位
  for (const p of (showTop5.value ? d.top5 || [] : [])) {
    L.marker(p.coord, {
      icon: L.divIcon({
        className: '',
        html: `<div style="background:${p.color};color:#fff;width:24px;height:24px;`
            + `border-radius:50%;display:flex;align-items:center;justify-content:center;`
            + `font-weight:700;font-size:13px;border:2px solid #fff;`
            + `box-shadow:0 0 8px rgba(0,0,0,.7)">${p.rank}</div>`,
        iconSize: [24, 24], iconAnchor: [12, 12],
      }),
    }).addTo(layerGroup).bindPopup(
      `<b>第 ${p.rank} 严重点位 — ${p.grade}</b><br>`
      + `时间：${p.time}<br>`
      + `原始综合指数：${p.raw_score ?? '—'}<br>`
      + `时空增强排序评分：${p.score}<br>`
      + `氨氮：${p.ammonia ?? '—'} mg/L<br>`
      + `溶解氧：${p.do ?? '—'} mg/L<br>`
      + `COD：${p.cod ?? '—'} mg/L<br>`
      + `浊度：${p.turbidity ?? '—'} NTU`,
    )
  }
  const selected=d.samples?.find(s=>s.id === selectedId.value)
  if (selected) {
    const node=document.createElement('div')
    node.className='sample-popup'
    node.textContent=[`采样点 #${selected.id+1} · ${selected.grade}`, selected.time || '时间未提供',
      ...sampleMetrics.map(([key,label,unit])=>`${label}：${numberText(selected.values[key])} ${unit}`
        + (selected.values[key] == null && selected.interpolated[key] != null ? `（缺测；插值参考 ${numberText(selected.interpolated[key])}）` : '')),
      `原始综合指数：${numberText(selected.raw_score)}`].join('\n')
    L.circleMarker(selected.coord,{radius:8,color:'#fff',weight:3,fillColor:'#159aff',fillOpacity:1})
      .addTo(layerGroup).bindPopup(node,{minWidth:240,maxWidth:310,maxHeight:280}).openPopup()
  }
}

async function load() {
  const version = ++loadVersion
  selectedId.value = null; nearbyIds.value = []
  if (props.simulation) { data.value=null;render(true);return }
  if (!store.status.has_data) { data.value = null; loading.value = false; render(); return }
  loading.value = true
  try {
    const result = await api.traceMap()
    if (disposed || version !== loadVersion) return
    data.value = result
    render(true)
  } catch (e) {
    if (!disposed && version === loadVersion) toastErr(`地图数据加载失败：${e.message}`)
  } finally {
    if (version === loadVersion) loading.value = false
  }
}

onMounted(() => { initMap(); load(); document.addEventListener('fullscreenchange', updateFullscreen) })
onBeforeUnmount(() => {
  disposed = true; loadVersion++; sourceVersion++; resizeObserver?.disconnect()
  document.removeEventListener('fullscreenchange', updateFullscreen)
  map?.remove(); map = null; layerGroup = null
})

watch(() => [store.status.has_data, store.status.black_spot_count, store.status.analysis_revision, store.status.detection_algorithm_version, store.status.results_revision], load)
watch([showHeat, showClusters, showTop5, showTrack, heatRadius, heatOpacity], () => render())
watch(selectedId,()=>render())
watch(()=>props.simulation,load)
watch(()=>props.frameIndex,()=>render())
watch(() => [store.config.keys.starcloud, store.config.keys.amap], ([starcloud, amap]) => {
  if (starcloud && !tried.has('starcloud')) selectSource('starcloud')
  else if (amap && !tried.has('amap') && !tried.has('starcloud')) selectSource('amap')
})
</script>

<template>
  <div ref="wrapper" class="wrap">
    <p v-if="!simulation && store.status.detection_outdated" class="map-warning">已有热点使用旧算法，请更新检测以重新计算热点与排序。
      <button type="button" :disabled="store.loading || store.chatRunning" @click="refreshDetection">更新检测</button>
    </p>
    <div class="basemap-options">
      <label>地图底图
        <select :value="provider" @change="selectSource($event.target.value)">
          <option value="starcloud">星图卫星影像{{ store.config.keys.starcloud ? '' : '（需配置 Token）' }}</option>
          <option value="amap">高德街道图{{ store.config.keys.amap ? '' : '（需配置 Web服务 Key）' }}</option>
          <option value="esri">Esri 卫星图</option>
          <option value="osm">OSM 街道图</option>
        </select>
      </label>
      <button type="button" @click="selectSource(provider)">重试底图</button>
      <button type="button" @click="toggleFullscreen">{{ fullscreen ? '退出全屏' : '全屏查看' }}</button>
      <span role="status" :class="{ 'map-error':baseState === 'error' }">{{ baseMessage }}</span>
    </div>
    <p v-if="fallbackReason" class="map-warning">{{ fallbackReason }}</p>
    <p v-if="baseState === 'error'" class="map-warning">底图暂不可用，轨迹与热力图仍可查看。请检查网络或切换底图。</p>
    <div v-if="!simulation" class="layer-options">
      <label><input v-model="showHeat" type="checkbox" />严重程度热力图</label>
      <label><input v-model="showTrack" type="checkbox" />走航轨迹</label>
      <label><input v-model="showClusters" type="checkbox" />聚类范围</label>
      <label><input v-model="showTop5" type="checkbox" />Top5 点位</label>
      <details class="heat-settings">
        <summary>热力显示</summary>
        <div class="heat-sliders">
          <label>色带宽度<input v-model.number="heatRadius" aria-label="热力显示半径" type="range" min="12" max="42" step="1" /><span>{{ heatRadius }} px</span></label>
          <label>颜色强度<input v-model.number="heatOpacity" aria-label="热力不透明度" type="range" min="0.4" max="1" step="0.02" /><span>{{ Math.round(heatOpacity*100) }}%</span></label>
        </div>
      </details>
    </div>
    <p v-if="!simulation && data?.samples?.length" class="click-hint">点击污染色带查看最近采样点；同位置多条记录可按时间切换。</p>
    <div v-if="!store.status.has_data && !simulation" class="tip">
      上传走航数据后显示严重程度热力图；完成黑臭检测后叠加热点和严重点位。
    </div>
    <div class="map-shell">
      <div ref="el" class="map"></div>
      <div v-if="data" class="legend">
      <div class="lg-title">相对 {{ data.target_water_class }} 类的综合污染指数</div>
      <div class="gradient"></div>
      <div class="ticks"><span v-for="v in [0,1,2,3,5,8]" :key="v" :style="{left:`${v/8*100}%`}">{{ v === 8 ? '≥8' : v }}</span></div>
        <div class="lg-note">绿 → 黄 → 橙 → 红 · 数字为 Top5</div>
      </div>
      <div v-else-if="simulation" class="legend">
        <div class="lg-title">模拟{{ simulation.label }} · mg/L</div>
        <div class="gradient" :class="{'inverse-gradient':simulation.color_scale.inverse}"></div>
        <div class="sim-ticks"><span>{{ numberText(simulation.color_scale.min) }}</span><span>{{ numberText(simulation.color_scale.max) }}</span></div>
        <div class="lg-note">本次模拟全时段统一色标 · 点位可点击</div>
      </div>
    </div>
    <section v-if="selectedSample && !simulation" class="sample-detail" aria-label="采样点水质详情">
      <div class="sample-heading"><b>采样点 #{{ selectedSample.id+1 }} · {{ selectedSample.grade }}</b><button type="button" @click="selectedId=null">关闭</button></div>
      <p>{{ selectedSample.time || '时间未提供' }} · WGS-84 {{ numberText(selectedSample.coord[0],6) }}, {{ numberText(selectedSample.coord[1],6) }}</p>
      <label v-if="nearbyIds.length>1">附近记录（{{ nearbyIds.length }} 条）
        <select v-model.number="selectedId"><option v-for="id in nearbyIds" :key="id" :value="id">#{{ id+1 }} · {{ data.samples.find(s=>s.id===id)?.time }}</option></select>
      </label>
      <div class="sample-metrics"><div v-for="row in sampleRows" :key="row.key"><span>{{ row.label }}</span><b>{{ numberText(row.value) }} {{ row.unit }}</b><small v-if="row.value == null && row.interpolated != null">缺测 · 插值参考 {{ numberText(row.interpolated) }}</small><small v-else>{{ row.value == null ? '缺测' : row.source === 'observed' ? '原始观测' : '记录值（无插值标记）' }}</small></div></div>
      <p>原始综合污染指数：{{ numberText(selectedSample.raw_score) }}</p>
    </section>
    <p v-if="!simulation" class="map-footnote">沿线颜色表示相对 {{ data?.target_water_class || store.status.target_water_class }} 类的污染指数；横向晕染仅供显示，不表示污染范围。≥8 使用上限色，原始值见点位详情。</p>
    <p v-else class="map-footnote">{{ simulation.mapping.reason }} {{ simulation.notice }}</p>
  </div>
</template>

<style scoped>
.wrap { position: relative; }
.map :deep(.sample-popup) { white-space:pre-line;min-width:220px;line-height:1.6;overflow-wrap:break-word; }
.click-hint { color:var(--c-sub);font-size:12px;margin:6px 0 10px; }
.sample-detail { margin-top:12px;padding:14px;background:#1b2c3c;border:1px solid #3f718f;border-radius:8px;font-size:12px; }
.sample-detail p { color:var(--c-sub);line-height:1.6; }
.sample-heading { display:flex;align-items:center;justify-content:space-between;gap:12px; }
.sample-heading button { color:var(--c-sub);background:transparent;border:0;cursor:pointer; }
.sample-detail select { max-width:100%;background:#14222f;color:var(--c-text);padding:5px;border:1px solid var(--c-border); }
.sample-metrics { display:flex;flex-wrap:wrap;gap:10px;margin-top:12px; }
.sample-metrics>div { display:grid;gap:5px;min-width:105px;flex:1;background:#132330;padding:10px;border-radius:5px; }
.sample-metrics small { color:var(--c-sub); }
.sim-ticks { display:flex;justify-content:space-between;margin-top:3px; }
.inverse-gradient { transform:scaleX(-1); }
.map-shell { position:relative; }
.wrap:fullscreen { background:#111d29; padding:16px; overflow:auto; }
.wrap:fullscreen .map { height:calc(100dvh - 160px); min-height:350px; }
.basemap-options { display:flex; flex-wrap:wrap; align-items:center; gap:10px; margin-bottom:10px; color:var(--c-sub); font-size:12px; }
.basemap-options label { display:flex; align-items:center; gap:8px; }
.basemap-options select,.basemap-options button { background:var(--c-bg); color:var(--c-text); border:1px solid var(--c-border); border-radius:5px; padding:6px 9px; }
.map-warning,.map-error { color:#f0bc67; font-size:12px; }
.layer-options { display:flex; flex-wrap:wrap; align-items:center; gap:16px; margin-bottom:12px; color:var(--c-sub); font-size:12px; }
.layer-options label { display:flex; align-items:center; gap:5px; cursor:pointer; }
.map { height:clamp(460px,65vh,640px); border-radius: 8px; overflow: hidden; z-index: 0; }
.map-footnote { color:var(--c-sub); font-size:11px; line-height:1.6; margin:10px 0 0; }
.heat-settings { margin-left:auto; position:relative; }
.heat-settings summary { cursor:pointer; }
.heat-sliders { position:absolute; right:0; top:24px; z-index:600; width:270px; padding:12px; border:1px solid var(--c-border); border-radius:7px; background:#182634; box-shadow:0 5px 20px #0005; }
.heat-sliders label { display:flex; gap:10px; margin:5px 0; }
.heat-sliders input { width:110px; }
.tip { padding: 40px; text-align: center; color: var(--c-sub); font-size: 13px; }

.legend {
  position: absolute; left: 10px; bottom: 10px; z-index: 500;
  background: rgba(23,33,43,.94); border: 1px solid var(--c-border);
  border-radius: 7px; padding: 7px 10px; font-size: 10px; line-height: 1.6;
  pointer-events: none;
}
.lg-title { font-weight: 600; margin-bottom: 3px; }
.lg-item { display: flex; align-items: center; gap: 6px; color: var(--c-sub); }
.lg-item i { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
.lg-note { margin-top: 4px; padding-top: 4px; border-top: 1px solid var(--c-border); color: var(--c-sub); }
.gradient { width:205px; height:9px; border-radius:4px; background:linear-gradient(to right,#17a05c 0%,#58d352 12.5%,#ffd91e 25%,#ff7b18 37.5%,#ef2825 62.5%,#ac002d 100%); }
.ticks { position:relative; height:20px; color:var(--c-sub); }
.ticks span { position:absolute; transform:translateX(-50%); }
</style>
