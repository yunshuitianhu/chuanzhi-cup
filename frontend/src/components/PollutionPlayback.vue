<script setup>
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../api/client'
import { useSessionStore } from '../stores/session'
import { numberText } from '../utils/mapInteraction'
import { playbackClock } from '../utils/playback'
import EChart from './EChart.vue'

const SimulationMap=defineAsyncComponent(()=>import('./TraceMap.vue'))
const props=defineProps({revision:{type:Object,default:null}})
const store=useSessionStore()
const result=ref(null), constituent=ref('ammonia'), busy=ref(false), error=ref('')
const frame=ref(0), playing=ref(false), speed=ref(1), showMap=ref(true)
let disposed=false, controller=null, version=0
const currentTime=computed(()=>result.value?.times[frame.value] ?? 0)
const currentValues=computed(()=>result.value?.concentrations[frame.value] || [])
const framePeak=computed(()=>currentValues.value.length ? Math.max(...currentValues.value) : null)
const frameMinimum=computed(()=>currentValues.value.length ? Math.min(...currentValues.value) : null)
const clock=playbackClock(()=>{
  if (!result.value || frame.value >= result.value.times.length-1) { playing.value=false;return }
  frame.value++
  if (frame.value >= result.value.times.length-1) playing.value=false
})

watch([playing,speed],()=>{if(playing.value)clock.start(speed.value);else clock.stop()})
function togglePlaying() {
  if(!result.value || result.value.times.length<2)return
  if(frame.value===result.value.times.length-1 && !playing.value)frame.value=0
  playing.value=!playing.value
}
function seek(value) {playing.value=false;frame.value=Number(value)}

async function load() {
  const request=++version, workspace=store.workspaceRevision
  controller?.abort();controller=new AbortController()
  playing.value=false;clock.stop();frame.value=0;result.value=null;busy.value=true;error.value=''
  try {
    const payload=await api.riverPlayback(constituent.value,controller.signal)
    if(disposed || request!==version || workspace!==store.workspaceRevision)return
    result.value=payload
  } catch(e) {
    if(!disposed && request===version && e.name!=='AbortError')error.value=e.message
  } finally {if(request===version)busy.value=false}
}
onMounted(load)
onBeforeUnmount(()=>{disposed=true;version++;controller?.abort();clock.stop()})
watch([constituent,()=>props.revision,()=>store.status.analysis_revision],load)

const option=computed(()=>{
  const d=result.value
  if(!d)return {series:[]}
  const lines=[]
  if(d.limit!=null)lines.push({yAxis:d.limit,name:`${d.target_water_class}类${d.limit_direction==='minimum'?'下限':'上限'} ${d.limit} mg/L`})
  const source=d.source.source_effective_chainage_m ?? d.source.chainage_m
  if(source!=null)lines.push({xAxis:source,name:'情景排放点'})
  return {
    animation:false,
    grid:{left:56,right:30,top:38,bottom:45},
    tooltip:{trigger:'axis',valueFormatter:v=>`${numberText(v)} mg/L`},
    title:{text:`${d.label}沿程浓度 · T+${numberText(currentTime.value)} h`,textStyle:{color:'#dae7f3',fontSize:14},left:12},
    xAxis:{type:'value',name:'桩号 m',nameLocation:'middle',nameGap:28,axisLabel:{color:'#95abbe'},splitLine:{lineStyle:{color:'#263848'}}},
    yAxis:{type:'value',name:'mg/L',min:0,max:Math.max(d.color_scale.max,d.limit ?? 0)*1.1 || 1,
      axisLabel:{color:'#95abbe'},splitLine:{lineStyle:{color:'#263848'}}},
    series:[{type:'line',showSymbol:d.chainage.length<20,smooth:false,
      data:d.chainage.map((x,i)=>[x,currentValues.value[i]]),lineStyle:{color:'#32bbf4',width:3},
      areaStyle:{color:'#2588bd',opacity:.12},markLine:{silent:true,symbol:'none',label:{color:'#9bb0bf',formatter:'{b}'},data:lines}}],
  }
})
</script>

<template>
  <div class="pollution-player">
    <div class="player-toolbar">
      <label>模拟指标<select v-model="constituent"><option value="ammonia">氨氮</option><option value="do">溶解氧</option><option value="cbod">CBOD</option></select></label>
      <button type="button" :disabled="busy || store.chatRunning" @click="load">{{busy?'加载中…':'加载当前模拟结果'}}</button>
      <span class="player-tag">恒定流情景模拟</span>
    </div>
    <div v-if="!result && !busy" class="player-empty">
      <p>{{error || '当前项目还没有污染扩散模拟结果。'}}</p>
      <p>在<RouterLink to="/river">河道模型</RouterLink>中设置河段参数，依次初始化、运行水动力和模拟污染事件，然后在这里播放。</p>
    </div>
    <template v-if="result">
      <div class="playback-controls">
        <button type="button" :disabled="result.times.length<2" @click="togglePlaying">{{playing?'暂停':'播放扩散'}}</button>
        <button type="button" :disabled="frame===0" @click="seek(frame-1)">上一帧</button>
        <button type="button" :disabled="frame===result.times.length-1" @click="seek(frame+1)">下一帧</button>
        <button type="button" @click="seek(0)">复位</button>
        <label>演示速度<select v-model.number="speed"><option :value=".5">0.5×</option><option :value="1">1×</option><option :value="2">2×</option><option :value="4">4×</option></select></label>
        <strong>T+{{numberText(currentTime)}} h</strong>
      </div>
      <input class="time-slider" aria-label="污染扩散时间轴" type="range" min="0" :max="result.times.length-1" :value="frame" @input="seek($event.target.value)" />
      <div class="player-stats">
        <span>当前峰值 <b>{{numberText(framePeak)}} mg/L</b></span><span>当前最低 <b>{{numberText(frameMinimum)}} mg/L</b></span>
        <span>第 {{frame+1}} / {{result.times.length}} 帧</span><span>原始输出 {{result.total_frames}} 帧</span>
      </div>
      <EChart :option="option" :height="280" />
      <p class="source-info">{{result.river_name}} · 情景排放{{result.source.pollutant_type==='ammonia'?'氨氮':result.source.pollutant_type==='cbod'?'CBOD':'组分未提供'}} · 开始 T+{{numberText(result.source.source_start_h)}} h · 持续 {{numberText(result.source.duration_min)}} min · 设定负荷 {{numberText(result.source.load_kg)}} kg</p>
      <label v-if="result.mapping.available" class="show-map"><input v-model="showMap" type="checkbox" />同步显示卫星地图扩散</label>
      <SimulationMap v-if="result.mapping.available && showMap" :simulation="result" :frame-index="frame" />
      <p v-if="!result.mapping.available" class="mapping-note">地图暂不可用：{{result.mapping.reason}}</p>
      <p class="player-note">{{result.notice}} {{result.constituent==='cbod'?'CBOD没有直接对应的GB3838类别限值，不用COD限值替代。':''}}</p>
    </template>
  </div>
</template>

<style scoped>
.player-toolbar,.playback-controls,.player-stats {display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin-bottom:12px;}
.player-toolbar label,.playback-controls label {display:flex;align-items:center;gap:8px;}
button,select {padding:7px 11px;border:1px solid var(--c-border);border-radius:6px;background:#172634;color:var(--c-text);font-size:12px;}
button {cursor:pointer;}button:disabled {opacity:.4;cursor:default;}
.playback-controls>button:first-child {background:#168ac1;color:#fff;}
.playback-controls strong {margin-left:auto;color:#73d1fb;}
.player-tag {color:#88cdb0;border:1px solid #386653;padding:4px 8px;border-radius:10px;font-size:12px;}
.time-slider {width:100%;accent-color:#29ace5;}
.player-stats {margin-top:12px;color:var(--c-sub);font-size:12px;gap:20px;}
.player-stats b {color:var(--c-text);}
.player-empty {padding:18px;background:#152331;border-radius:7px;color:var(--c-sub);font-size:13px;line-height:1.8;}
.player-empty a {color:#54beec;}
.player-note,.source-info,.mapping-note {color:var(--c-sub);font-size:12px;line-height:1.7;}
.mapping-note {color:#dfb876;}
.show-map {display:flex;align-items:center;gap:7px;margin:16px 0;color:var(--c-sub);font-size:12px;}
</style>
