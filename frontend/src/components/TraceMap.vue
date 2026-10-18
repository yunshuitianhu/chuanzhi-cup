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
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { severityLayer } from '../utils/severityLayer'
import { amapStaticLayer } from '../utils/amapStaticLayer'
import { displayTrace, wgsToGcj, gcjToWgs } from '../utils/mapCoordinates'
import { api } from '../api/client'
import { useSessionStore } from '../stores/session'
import { toastErr } from '../composables/useToast'

const store = useSessionStore()
const el = ref(null)
const data = ref(null)
const loading = ref(false)
const showHeat = ref(true), showClusters = ref(false), showTop5 = ref(true)
let map = null
let layerGroup = null
let baseLayer = null, resizeObserver = null, disposed = false, loadVersion = 0, sourceVersion = 0
const provider = ref(store.config.keys.amap ? 'amap' : 'esri')
const baseState = ref('loading'), baseMessage = ref('正在加载底图…'), fallbackReason = ref('')
const tried = new Set()
const sourceNames = {amap:'高德街道图', esri:'Esri 卫星图', osm:'OSM 街道图'}

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
  map.setMaxZoom(source === 'amap' ? 17 : 19)
  map.setView(center, Math.min(map.getZoom(), source === 'amap' ? 17 : 19), {animate:false})
  baseState.value = 'loading'; baseMessage.value = `正在加载${sourceNames[source]}…`
  let successes = 0, failures = 0, fallbackQueued = false
  const report = (state, message) => {
    if (disposed || version !== sourceVersion) return
    baseState.value = state; baseMessage.value = message
    if (state !== 'error' || fallbackQueued) return
    const next = ['amap','esri','osm'].find(s => !tried.has(s) && (s !== 'amap' || store.config.keys.amap))
    if (next) {
      fallbackQueued = true
      fallbackReason.value = `${sourceNames[source]}加载失败：${message}；已尝试备用底图。`
      queueMicrotask(() => { if (!disposed && version === sourceVersion) selectSource(next, true) })
    }
  }
  if (source === 'amap') baseLayer = amapStaticLayer(api.amapStaticMap, report)
  else {
    const osm = source === 'osm'
    baseLayer = L.tileLayer(osm
      ? 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
      : 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
      maxZoom:19, attribution:osm ? '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>' : 'Esri',
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
  selectSource(provider.value)
  resizeObserver = new ResizeObserver(() => map?.invalidateSize())
  resizeObserver.observe(el.value)
}

function render(fit = false) {
  if (!map || !layerGroup) return
  layerGroup.clearLayers()
  const d = displayTrace(data.value, provider.value === 'amap')
  if (!d) return

  // 轨迹
  if (d.track?.length) {
    const line = L.polyline(d.track, { color: '#2ea3f2', weight: 2, opacity: .75 })
    line.addTo(layerGroup)
    line.bindPopup(`走航轨迹 · ${d.track.length} 个点`)
    if (fit) map.fitBounds(line.getBounds(), { padding: [24, 24], maxZoom:17 })
  }

  // 邻域平均严重程度；密集采样不累加成更严重的颜色。
  if (showHeat.value && d.heat?.length) {
    severityLayer(d.heat).addTo(layerGroup)
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
      + `影响半径：${Math.round(c.radius_m)} m<br>`
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
}

async function load() {
  const version = ++loadVersion
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

onMounted(() => { initMap(); load() })
onBeforeUnmount(() => { disposed = true; loadVersion++; sourceVersion++; resizeObserver?.disconnect(); map?.remove(); map = null; layerGroup = null })

watch(() => [store.status.has_data, store.status.black_spot_count, store.status.analysis_revision], load)
watch([showHeat, showClusters, showTop5], () => render())
watch(() => store.config.keys.amap, (ready) => { if (ready && !tried.has('amap')) selectSource('amap') })
</script>

<template>
  <div class="wrap">
    <div class="basemap-options">
      <label>地图底图
        <select :value="provider" @change="selectSource($event.target.value)">
          <option value="amap">高德街道图{{ store.config.keys.amap ? '' : '（需配置 Web服务 Key）' }}</option>
          <option value="esri">Esri 卫星图</option>
          <option value="osm">OSM 街道图</option>
        </select>
      </label>
      <button type="button" @click="selectSource(provider)">重试底图</button>
      <span role="status" :class="{ 'map-error':baseState === 'error' }">{{ baseMessage }}</span>
    </div>
    <p v-if="fallbackReason" class="map-warning">{{ fallbackReason }}</p>
    <p v-if="baseState === 'error'" class="map-warning">底图暂不可用，轨迹与热力图仍可查看。请检查网络或切换底图。</p>
    <div class="layer-options">
      <label><input v-model="showHeat" type="checkbox" />严重程度热力图</label>
      <label><input v-model="showClusters" type="checkbox" />聚类范围</label>
      <label><input v-model="showTop5" type="checkbox" />Top5 点位</label>
    </div>
    <div v-if="!store.status.has_data" class="tip">
      上传走航数据后显示严重程度热力图；完成黑臭检测后叠加热点和严重点位。
    </div>
    <div ref="el" class="map"></div>
    <div v-if="data" class="legend">
      <div class="lg-title">相对 {{ data.target_water_class }} 类的综合污染指数</div>
      <div class="gradient"></div>
      <div class="ticks"><span v-for="v in [0,1,2,3,5,8]" :key="v" :style="{left:`${v/8*100}%`}">{{ v === 8 ? '≥8' : v }}</span></div>
      <div class="lg-note">绿 → 黄 → 橙 → 红：程度逐渐加重<br>指数 ≥8 按上限色阶平滑，原始值见点位详情<br>邻域平滑仅供展示，不代表未采样水域实测值</div>
      <div class="lg-note">虚线圆 = 聚类范围 · 数字 = Top5 严重点位</div>
    </div>
  </div>
</template>

<style scoped>
.wrap { position: relative; }
.basemap-options { display:flex; flex-wrap:wrap; align-items:center; gap:10px; margin-bottom:10px; color:var(--c-sub); font-size:12px; }
.basemap-options label { display:flex; align-items:center; gap:8px; }
.basemap-options select,.basemap-options button { background:var(--c-bg); color:var(--c-text); border:1px solid var(--c-border); border-radius:5px; padding:6px 9px; }
.map-warning,.map-error { color:#f0bc67; font-size:12px; }
.layer-options { display:flex; flex-wrap:wrap; gap:16px; margin-bottom:10px; color:var(--c-sub); font-size:12px; }
.layer-options label { display:flex; align-items:center; gap:5px; cursor:pointer; }
.map { height: 460px; border-radius: 8px; overflow: hidden; z-index: 0; }
.tip { padding: 40px; text-align: center; color: var(--c-sub); font-size: 13px; }

.legend {
  position: absolute; left: 10px; bottom: 10px; z-index: 500;
  background: rgba(23,33,43,.94); border: 1px solid var(--c-border);
  border-radius: 7px; padding: 8px 11px; font-size: 11px; line-height: 1.7;
  pointer-events: none;
}
.lg-title { font-weight: 600; margin-bottom: 3px; }
.lg-item { display: flex; align-items: center; gap: 6px; color: var(--c-sub); }
.lg-item i { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
.lg-note { margin-top: 4px; padding-top: 4px; border-top: 1px solid var(--c-border); color: var(--c-sub); }
.gradient { width: 235px; height: 12px; border-radius: 4px; background: linear-gradient(to right,#2e8b57 0%,#66be5a 12.5%,#ffc83c 25%,#ff7323 37.5%,#dc233c 62.5%,#780023 100%); }
.ticks { position:relative; height:20px; color:var(--c-sub); }
.ticks span { position:absolute; transform:translateX(-50%); }
</style>
