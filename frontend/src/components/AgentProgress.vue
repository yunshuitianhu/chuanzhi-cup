<script setup>
import {computed} from 'vue'
const props=defineProps({run:Object,elapsed:{type:Number,default:0},connectionError:String})
defineEmits(['stop','sync'])
const labels={get_data_summary:'读取监测摘要',find_black_spots:'筛查异常点',analyze_video:'分析视频线索',
  cross_validate_video:'核对视频与监测记录',get_comprehensive_summary:'整理综合证据',
  search_treatment_cases:'检索治理案例',generate_final_report:'生成分析报告',reverse_geocode:'核对地理背景',
  search_nearby_pollution_sources:'查询周边排查线索',get_national_station_data:'查询国控监测背景',
  get_satellite_image:'获取卫星背景',init_river_model:'初始化河段情景',run_hydrodynamic_simulation:'计算恒定流水动力',
  simulate_pollution_event:'模拟污染事件',get_concentration_profile:'读取情景浓度',compare_scenarios:'比较情景',export_river_model_data:'导出模型数据'}
const statusLabels={running:'进行中',stopping:'等待停止',succeeded:'已完成',partial:'部分完成',failed:'失败',cancelled:'已停止',skipped:'已跳过'}
const active=computed(()=>props.run?.steps?.findLast(step=>step.status==='running'))
const stage=computed(()=>props.run?.stop_requested && props.run?.busy?'停止请求已收到，等待当前调用收尾':
  active.value ? labels[active.value.name]||active.value.name : props.run?.stage)
</script>

<template>
  <section v-if="run?.run_id || connectionError" class="progress" aria-live="polite">
    <div class="head">
      <strong>{{ stage || '核对任务状态' }}</strong>
      <span>{{ statusLabels[run?.status] || '' }} · {{ Math.floor(elapsed) }}秒</span>
      <button v-if="run?.busy" :disabled="run.stop_requested" @click="$emit('stop')">{{ run.stop_requested ? '等待当前调用收尾…' : '停止本轮' }}</button>
      <button @click="$emit('sync')">核对任务状态</button>
    </div>
    <p v-if="run?.run_id">第 {{ run.turn }} 轮 · 本轮已调用 {{ run.tool_count ?? run.steps?.length ?? 0 }} 次工具</p>
    <p v-if="run?.stop_requested && run?.busy">停止请求会阻止后续步骤；当前网络或计算调用收尾前，数据操作和重新分析仍保持锁定。</p>
    <p v-if="connectionError" class="error">{{ connectionError }}</p>
    <ol v-if="run?.steps?.length">
      <li v-for="step in run.steps" :key="step.id">
        <span>{{ labels[step.name] || step.name }}</span>
        <span>{{ statusLabels[step.status] || step.status }} · {{ step.elapsed_s.toFixed(1) }}秒</span>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.progress { border:1px solid var(--c-border);background:var(--c-surface-2);border-radius:8px;padding:12px;margin-bottom:12px; }
.head { display:flex;gap:12px;align-items:center;flex-wrap:wrap; }
.head strong { flex:1; }.head span,p { font-size:12px;color:var(--c-sub); }
button { background:var(--c-surface);color:var(--c-ink);border:1px solid var(--c-border);border-radius:5px;padding:5px 8px; }
ol { padding:0;list-style:none;max-height:180px;overflow:auto; }
li { display:flex;justify-content:space-between;gap:12px;padding:5px 0;border-top:1px solid var(--c-border);font-size:12px; }
.error { color:#ffb3c1; }
</style>
