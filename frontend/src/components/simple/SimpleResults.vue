<script setup>
import {ref,computed,onMounted,onBeforeUnmount,watch} from 'vue'
import {useSessionStore} from '../../stores/session'
import {useSimpleWorkflow} from '../../composables/useSimpleWorkflow'
import {api} from '../../api/client'
import TraceMap from '../TraceMap.vue'
import EChart from '../EChart.vue'
const store=useSessionStore(),summary=useSimpleWorkflow(store),error=ref(''),series=ref(null)
let version=0,controller
const gpsCount=ref(null)
const scope=()=>JSON.stringify([store.workspaceRevision,store.projectState.current?.id,store.status.analysis_revision,store.status.results_revision])
async function load(){const token=++version,key=scope();controller?.abort();controller=new AbortController();series.value=null
 if(!store.status.has_data)return
 try{const data=await api.timeSeries(controller.signal);if(token===version&&key===scope())series.value=data}catch(e){if(token===version&&e.name!=='AbortError')error.value=e.message}}
onMounted(()=>{load();if(store.status.has_detection&&!store.blackSpots)store.refreshResults()});watch(scope,load);onBeforeUnmount(()=>{version++;controller?.abort()})
async function detect(){error.value='';try{await store.runBlackSpots()}catch(e){error.value=e.message}}
const option=computed(()=>{const p=series.value?.panels?.[0];return {tooltip:{trigger:'axis'},grid:{left:55,right:20,bottom:45},xAxis:{type:'category',data:series.value?.time||[]},yAxis:{type:'value',name:p?.name||'指标'},series:p?[{type:'line',showSymbol:false,data:p.values||p.data||[],lineStyle:{color:'#83d3cf'}}]:[]}})
</script>
<template><div><h2>读懂本次检测</h2><p>预警采样点和聚类属于规则派生线索，不是已确认污染源。</p><button class="simple-primary" :disabled="!summary.hasData||store.loading||store.chatRunning" @click="detect">{{store.loading?'检测中…':summary.hasDetection?'重新检测':'运行基础筛查'}}</button><p v-if="!summary.hasData">请先导入监测数据。</p><p v-else-if="summary.detection==='stale'">已有检测依据已过期，请重新检测。</p><p v-else-if="!summary.hasDetection">已读取数据，下一步运行基础筛查。</p><div class="simple-stats"><article><span>监测记录</span><strong>{{summary.records}}</strong></article><article><span>预警采样点</span><strong>{{summary.warningPoints??'待检测'}}</strong></article><article><span>聚类线索</span><strong>{{summary.clusters??'待获取'}}</strong></article></div><p v-if="summary.warningPoints===0">本次筛查未发现符合当前规则的预警点；不代表已排除全部污染风险。</p><p>{{store.status.time_range?.join(' 至 ')}}</p><p v-if="store.sampleLastDetection?.requested && !store.sampleLastDetection.ok" role="alert">样例已载入，检测未完成：{{store.sampleLastDetection.error}}</p><p v-if="error" role="alert">{{error}}</p><template v-if="summary.hasData"><h3>采样记录地图</h3><p>每个点属于设备在不同时间采集的记录，地图颜色不表示同一时刻的扩散过程。</p><TraceMap v-if="gpsCount!==0" @availability="gpsCount=$event" /><p v-else>可以查看指标变化，但无法定位河道上的具体异常位置。</p><h3>采样时间趋势</h3><p>横轴是设备采样时间，不是污染传播时间。</p><EChart :option="option" :height="260" /></template><RouterLink to="/charts">查看专业图表与原始记录 →</RouterLink></div></template>
