<script setup>
import { computed, useId } from 'vue'
import { trailProfile, trailIntervals, trailColor } from '../utils/simulationTrail'
import { numberText } from '../utils/mapInteraction'

const props = defineProps({ simulation:{type:Object,required:true}, frameIndex:{type:Number,default:0} })
const prefix = `schematic-${useId().replace(/[^a-z0-9-]/gi, '')}`
const profile = computed(() => trailProfile(props.simulation))
const axis = computed(() => props.simulation.chainage)
const position = value => {
  const start = axis.value[0], length = axis.value.at(-1) - start
  const t = length > 0 ? Math.max(0, Math.min(1, (value - start) / length)) : 0
  return [65 + 870 * t, 180 + 40 * Math.sin(t * 2 * Math.PI)]
}
const river = computed(() => Array.from({length:101}, (_, i) => position(axis.value[0] + (axis.value.at(-1) - axis.value[0]) * i / 100)).map(p => p.join(',')).join(' '))
const source = computed(() => {
  const x = props.simulation.source.source_effective_chainage_m ?? props.simulation.source.chainage_m
  return Number.isFinite(x) && x >= axis.value[0] && x <= axis.value.at(-1) ? { x, p:position(x) } : null
})
const segments = computed(() => trailIntervals(props.simulation, props.frameIndex, profile.value).map(s => {
  const start = axis.value[s.index - 1], length = axis.value[s.index] - start
  return { ...s, from:position(start + length * s.lo), to:position(start + length * s.hi) }
}))
</script>

<template>
  <section class="schematic" aria-label="按模型桩号播放的河道扩散示意图">
    <header><strong>河道扩散示意图</strong><span>模型桩号 · 未地理定位</span></header>
    <p>使用当前模拟结果播放；河道弯曲仅作示意，不对应真实地图位置。</p>
    <svg viewBox="0 0 1000 340" role="img" aria-label="假定污染源及当前模拟帧的沿河浓度变化">
      <defs>
        <pattern :id="`${prefix}-grid`" width="32" height="32" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r="1" fill="#68a8b4" opacity=".13" /></pattern>
        <linearGradient v-for="(s, i) in segments" :id="`${prefix}-${i}`" :key="i" gradientUnits="userSpaceOnUse" :x1="s.from[0]" :y1="s.from[1]" :x2="s.to[0]" :y2="s.to[1]">
          <stop offset="0" :stop-color="trailColor(s.a)" :stop-opacity="Math.pow(s.a,.45)" />
          <stop offset="1" :stop-color="trailColor(s.b)" :stop-opacity="Math.pow(s.b,.45)" />
        </linearGradient>
      </defs>
      <rect width="1000" height="340" :fill="`url(#${prefix}-grid)`" />
      <polyline :points="river" fill="none" stroke="#103541" stroke-width="55" stroke-linecap="round" />
      <polyline :points="river" fill="none" stroke="#4a8490" stroke-opacity=".28" stroke-width="1" stroke-dasharray="4 8" />
      <g class="schematic-trail" :data-segments="segments.length" fill="none" stroke-linecap="round">
        <g v-for="(s,i) in segments" :key="i" :stroke="`url(#${prefix}-${i})`">
          <path :d="`M${s.from} L${s.to}`" stroke-width="40" opacity=".12" />
          <path :d="`M${s.from} L${s.to}`" stroke-width="25" opacity=".28" />
          <path :d="`M${s.from} L${s.to}`" stroke-width="13" />
          <path :d="`M${s.from} L${s.to}`" stroke-width="3" opacity=".75" />
        </g>
      </g>
      <g v-if="source" class="schematic-source">
        <circle :cx="source.p[0]" :cy="source.p[1]" r="20" fill="#f43f5e" opacity=".13" />
        <circle :cx="source.p[0]" :cy="source.p[1]" r="8" fill="#f43f5e" stroke="#fff" stroke-width="3" />
        <path :d="`M${source.p[0]},${source.p[1]-25} v-35`" stroke="#a1c6cd" stroke-dasharray="3 4" />
        <text :x="Math.max(140,Math.min(860,source.p[0]))" :y="source.p[1]-75" text-anchor="middle">假定排放点 · {{numberText(source.x,0)}} m</text>
      </g>
      <text x="65" y="295">起点 {{numberText(axis[0],0)}} m</text>
      <text x="935" y="295" text-anchor="end">终点 {{numberText(axis.at(-1),0)}} m</text>
    </svg>
    <footer>
      <div class="scale"><span>{{simulation.constituent==='do'?'DO下降量':'浓度增量'}} · mg/L</span><i></i><span>0 — {{numberText(profile.max)}}</span></div>
      <span v-if="frameIndex===0">初始状态 · 仅显示假定污染源</span>
      <span v-else-if="!segments.length">当前帧无高于可视下限的增量，可跳到扩散峰值查看</span>
      <span v-else>当前帧相对初始值的变化 · 线宽仅作显示</span>
    </footer>
  </section>
</template>

<style scoped>
.schematic {margin:16px 0;border:1px solid #31515e;border-radius:12px;background:#0c1c27;overflow:hidden;color:#c3dce3;}
header {display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;padding:18px 20px 0;}header strong {color:#e6f4f8;font-size:18px;}header span {font-size:12px;color:#80b8c7;border:1px solid #2e535e;border-radius:20px;padding:4px 9px;}
p {font-size:13px;margin:10px 20px;color:#8daeb9;line-height:1.6;}svg {display:block;width:100%;height:auto;min-height:200px;}text {font:16px sans-serif;fill:#b5d3dc;}
footer {display:flex;gap:16px;justify-content:space-between;align-items:center;flex-wrap:wrap;padding:14px 20px;border-top:1px solid #243f4b;font-size:12px;color:#99bac6;}.scale {display:flex;align-items:center;flex-wrap:wrap;gap:8px;}.scale i {display:block;width:95px;height:6px;border-radius:8px;background:linear-gradient(90deg,#6f236b,#f43f5e 65%,#ffded5);}
@media(max-width:600px) {header,p,footer {padding-left:12px;padding-right:12px;}p {margin-left:0;margin-right:0;}text {font-size:22px;}}
</style>
