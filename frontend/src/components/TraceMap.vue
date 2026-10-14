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
 * 底图用 Esri 卫星图 + OSM，都不需要 API Key。
 * 注意坐标系：监测数据是 WGS-84，Esri/OSM 底图也是 WGS-84，**无偏移**。
 * 高德底图是 GCJ-02（有偏移），故默认不启用。
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import 'leaflet.heat'
import { api } from '../api/client'
import { useSessionStore } from '../stores/session'
import { toastErr } from '../composables/useToast'

const store = useSessionStore()
const el = ref(null)
const data = ref(null)
const loading = ref(false)
let map = null
let layerGroup = null

const BASE_LAYERS = {
  'Esri 卫星图 (WGS-84 无偏移)': L.tileLayer(
    'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    { maxZoom: 19, attribution: 'Esri' },
  ),
  'OSM 街道图 (WGS-84)': L.tileLayer(
    'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    { maxZoom: 19, attribution: 'OSM' },
  ),
  'CartoDB Voyager': L.tileLayer(
    'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png',
    { maxZoom: 19, subdomains: 'abc', attribution: 'CartoDB' },
  ),
}

function initMap() {
  if (map || !el.value) return
  map = L.map(el.value, { zoomControl: true, attributionControl: true })
  BASE_LAYERS['Esri 卫星图 (WGS-84 无偏移)'].addTo(map)
  L.control.layers(BASE_LAYERS).addTo(map)
  layerGroup = L.layerGroup().addTo(map)
  map.setView([23.17, 113.12], 14)   // 默认视角；有数据后 fitBounds 覆盖
}

function render() {
  if (!map || !layerGroup) return
  layerGroup.clearLayers()
  const d = data.value
  if (!d) return

  // 轨迹
  if (d.track?.length) {
    const line = L.polyline(d.track, { color: '#2ea3f2', weight: 2, opacity: .75 })
    line.addTo(layerGroup)
    line.bindPopup(`走航轨迹 · ${d.track.length} 个点`)
    map.fitBounds(line.getBounds(), { padding: [24, 24] })
  }

  // 热力图（黑点密度）
  if (d.heat?.length) {
    L.heatLayer(d.heat, {
      radius: 18, blur: 14, maxZoom: 17, minOpacity: .25,
      gradient: { 0.2: '#2E8B57', 0.4: '#FFA500', 0.6: '#FF4500', 0.8: '#DC143C', 1: '#8B0000' },
    }).addTo(layerGroup)
  }

  // 聚类热点
  for (const c of d.clusters || []) {
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
      + `内梅罗均值：${c.avg_score}<br>`
      + `影响半径：${Math.round(c.radius_m)} m<br>`
      + `时间：${c.time_range[0].slice(11, 19)} ~ ${c.time_range[1].slice(11, 19)}<br>`
      + `氨氮均值：${c.ammonia_mean ?? '—'} mg/L<br>`
      + `DO 均值：${c.do_mean ?? '—'} mg/L`,
    )
  }

  // Top-5 严重点位
  for (const p of d.top5 || []) {
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
      + `内梅罗指数：${p.score}<br>`
      + `氨氮：${p.ammonia ?? '—'} mg/L<br>`
      + `溶解氧：${p.do ?? '—'} mg/L<br>`
      + `COD：${p.cod ?? '—'} mg/L<br>`
      + `浊度：${p.turbidity ?? '—'} NTU`,
    )
  }
}

async function load() {
  if (!store.status.has_black_spots) { data.value = null; render(); return }
  loading.value = true
  try {
    data.value = await api.traceMap()
    render()
  } catch (e) {
    toastErr(`地图数据加载失败：${e.message}`)
  } finally {
    loading.value = false
  }
}

onMounted(() => { initMap(); load() })
onBeforeUnmount(() => { map?.remove(); map = null; layerGroup = null })

watch(() => store.status.black_spot_count, load)
</script>

<template>
  <div class="wrap">
    <div v-if="!store.status.has_black_spots" class="tip">
      运行「黑臭检测」后此地图会显示轨迹、热点与严重点位。
    </div>
    <div ref="el" class="map"></div>
    <div v-if="data" class="legend">
      <div class="lg-title">GB3838-2002 污染等级（内梅罗指数）</div>
      <div class="lg-item"><i style="background:#2E8B57"></i>达 V 类</div>
      <div class="lg-item"><i style="background:#FFA500"></i>轻污染</div>
      <div class="lg-item"><i style="background:#FF4500"></i>污染</div>
      <div class="lg-item"><i style="background:#DC143C"></i>重污染</div>
      <div class="lg-item"><i style="background:#8B0000"></i>严重污染</div>
      <div class="lg-note">虚线圆 = 聚类范围 · 数字 = Top5 严重点位</div>
    </div>
  </div>
</template>

<style scoped>
.wrap { position: relative; }
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
</style>
