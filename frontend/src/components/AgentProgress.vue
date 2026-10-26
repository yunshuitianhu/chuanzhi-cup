<script setup>
import {computed, nextTick, onMounted, onBeforeUnmount, ref, watch} from 'vue'
const props=defineProps({run:Object,elapsed:{type:Number,default:0},connectionError:String})
const emit=defineEmits(['stop','sync'])
const emitStop=()=>emit('stop')
const emitSync=()=>emit('sync')
const labels={
  get_data_summary:'读取监测摘要',
  find_black_spots:'筛查异常点',
  analyze_video:'分析视频线索',
  cross_validate_video:'核对视频与监测记录',
  get_comprehensive_summary:'整理综合证据',
  search_treatment_cases:'检索治理案例',
  generate_final_report:'生成分析报告',
  reverse_geocode:'核对地理背景',
  search_nearby_pollution_sources:'查询周边排查线索',
  get_national_station_data:'查询国控监测背景',
  get_satellite_image:'获取卫星背景',
  init_river_model:'初始化河段情景',
  run_hydrodynamic_simulation:'计算恒定流水动力',
  simulate_pollution_event:'模拟污染事件',
  get_concentration_profile:'读取情景浓度',
  compare_scenarios:'比较情景',
  export_river_model_data:'导出模型数据',
  get_analysis_card_catalog:'读取分析卡片目录',
  submit_analysis_cards:'提交分析卡片'
}
const statusLabels={running:'进行中',stopping:'等待停止',succeeded:'已完成',partial:'部分完成',failed:'失败',cancelled:'已停止',skipped:'已跳过'}
const MAX_NOTICE=96
const safeText=(value,limit=MAX_NOTICE)=>{
  if(typeof value!=='string') return ''
  const text=value.trim()
  return text.length>limit ? text.slice(0,limit)+'…' : text
}
const toolLabel=name=>labels[name] || safeText(name,40) || '工具步骤'
const statusLabel=status=>statusLabels[status] || (status ? safeText(status,20) : '状态未知')
const stepTime=step=>{
  const n=Number(step?.elapsed_s)
  return Number.isFinite(n) && n>=0 ? `${n.toFixed(1)}秒` : ''
}
const isFinishedStatus=status=>['succeeded','partial','failed','cancelled'].includes(status)
const steps=computed(()=>Array.isArray(props.run?.steps) ? props.run.steps.filter(step => step && typeof step === "object") : [])
const activeStep=computed(()=>{
  for(let i=steps.value.length-1;i>=0;i--){
    if(steps.value[i]?.status==='running') return steps.value[i]
  }
  return null
})
const latestStep=computed(()=>activeStep.value || steps.value[steps.value.length-1] || null)
const elapsedSeconds=computed(()=>{
  const prop=Number(props.elapsed)
  if(Number.isFinite(prop) && prop>0) return prop
  const server=Number(props.run?.elapsed_s)
  return Number.isFinite(server) && server>0 ? server : 0
})
const elapsedText=computed(()=>`${Math.floor(elapsedSeconds.value)}秒`)
const currentOperation=computed(()=>{
  const run=props.run
  if(run?.stop_requested && run?.busy) return '停止请求已收到，等待当前调用收尾'
  if(activeStep.value) return `正在执行：${toolLabel(activeStep.value.name)}`
  const stage=safeText(run?.stage,60)
  if(run?.busy || run?.status==='running' || run?.status==='stopping'){
    if(/等待.*模型|模型.*等待|请求模型/.test(stage)) return '等待模型返回…'
    if(/等待.*工具|工具.*等待|执行工具/.test(stage)) return '等待工具完成…'
    if(/生成|整理|撰写|回答|正文|报告/.test(stage)) return '正在整理回答…'
    return stage || '等待模型返回…'
  }
  if(run?.status==='succeeded') return '本轮已完成'
  if(run?.status==='partial') return '部分完成'
  if(run?.status==='failed') return '执行失败'
  if(run?.status==='cancelled') return '本轮已停止'
  return stage || '核对任务状态'
})
const overallStatus=computed(()=>statusLabels[props.run?.status] || '状态未知')
const counts=computed(()=>{
  const out={running:0,succeeded:0,partial:0,failed:0,cancelled:0,skipped:0}
  for(const step of steps.value){
    const status=step?.status
    if(status && Object.prototype.hasOwnProperty.call(out,status)) out[status] += 1
  }
  return out
})
const countParts=computed(()=>{
  const c=counts.value
  const parts=[]
  if(c.running) parts.push(`${statusLabels.running} ${c.running}`)
  if(c.succeeded) parts.push(`${statusLabels.succeeded} ${c.succeeded}`)
  if(c.failed) parts.push(`${statusLabels.failed} ${c.failed}`)
  if(c.skipped) parts.push(`${statusLabels.skipped} ${c.skipped}`)
  if(c.partial) parts.push(`${statusLabels.partial} ${c.partial}`)
  if(c.cancelled) parts.push(`${statusLabels.cancelled} ${c.cancelled}`)
  return parts
})
const honestNotice=computed(()=>{
  const run=props.run
  if(!run) return ''
  if(run.busy && (run.stop_requested || run.status==='stopping')) return safeText(run.notice,120) || '停止请求会阻止后续步骤；当前调用收尾前操作仍保持锁定。'
  if(run.status==='failed') return '本轮执行失败；请查看失败步骤，已完成的步骤仍保留。'
  if(run.status==='partial') return '本轮部分完成；未完成或跳过的步骤已标记。'
  if(run.status==='cancelled') return '本轮已停止；已完成的部分保留。'
  if(counts.value.failed>0) return '存在失败步骤，请查看失败记录；不以部分步骤成功代表整轮完成。'
  return ''
})
const stepNotice=step=>{
  const notice=safeText(step?.notice)
  if(notice) return notice
  if(step?.status==='failed') return '该步骤未完成，未提供可展示的原因。'
  if(step?.status==='partial') return '该步骤仅部分完成。'
  if(step?.status==='cancelled') return '该步骤已停止。'
  if(step?.status==='skipped') return '该步骤已跳过。'
  return ''
}
const latestNotice=computed(()=>stepNotice(latestStep.value))
const historyEl=ref(null)
const autoFollow=ref(true)
const showLatest=ref(false)
const selecting=ref(false)
const isSelectingText=()=>{
  if(selecting.value) return true
  try{
    const selection=globalThis.getSelection?.()
    return !!(selection && selection.rangeCount>0 && !selection.isCollapsed)
  }catch{
    return false
  }
}
const nearBottom=()=>{
  const el=historyEl.value
  if(!el) return true
  const scrollHeight=Number(el.scrollHeight) || 0
  const scrollTop=Number(el.scrollTop) || 0
  const clientHeight=Number(el.clientHeight) || 0
  return scrollHeight - scrollTop - clientHeight <= 24
}
const scrollToLatest=()=>{
  const el=historyEl.value
  if(el){
    el.scrollTop=Math.max(0,(Number(el.scrollHeight) || 0) - (Number(el.clientHeight) || 0))
  }
  autoFollow.value=true
  showLatest.value=false
}
const onHistoryScroll=()=>{
  if(isSelectingText()){
    autoFollow.value=false
    showLatest.value=true
    return
  }
  if(nearBottom()){
    autoFollow.value=true
    showLatest.value=false
  }else{
    autoFollow.value=false
    showLatest.value=true
  }
}
const onSelectStart=()=>{
  selecting.value=true
  autoFollow.value=false
  showLatest.value=true
}
const onSelectEnd=()=>{
  selecting.value=false
}
const onHistoryKeydown=event=>{
  if(!event || event.shiftKey || event.ctrlKey || event.altKey || event.metaKey) return
  if(event.key==='End'){
    event.preventDefault?.()
    scrollToLatest()
  }else if(event.key==='Home'){
    event.preventDefault?.()
    const el=historyEl.value
    if(el) el.scrollTop=0
    autoFollow.value=false
    showLatest.value=true
  }
}
const historyOpen=ref(!isFinishedStatus(props.run?.status))
const failureSteps=computed(()=>steps.value.filter(step=>['failed','partial','cancelled','skipped'].includes(step?.status) || !!step?.notice))
const historySteps=computed(()=>historyOpen.value ? steps.value : failureSteps.value)
const historyLabel=computed(()=>historyOpen.value ? '收起历史' : `展开历史（${steps.value.length}）`)
const toggleHistory=async()=>{
  historyOpen.value=!historyOpen.value
  if(historyOpen.value && autoFollow.value){
    await nextTick()
    scrollToLatest()
  }
}
onMounted(()=>{
  globalThis.document?.addEventListener('pointerup',onSelectEnd)
  globalThis.document?.addEventListener('pointercancel',onSelectEnd)
  nextTick(()=>{
    if(autoFollow.value && !isSelectingText()) scrollToLatest()
  })
})
onBeforeUnmount(()=>{
  globalThis.document?.removeEventListener('pointerup',onSelectEnd)
  globalThis.document?.removeEventListener('pointercancel',onSelectEnd)
})
watch(()=>props.run?.run_id, async (id, old)=>{
  if(id===old) return
  autoFollow.value=true
  showLatest.value=false
  historyOpen.value=!isFinishedStatus(props.run?.status)
  await nextTick()
  scrollToLatest()
},{flush:'post'})
watch(()=>props.run?.status, status=>{
  if(isFinishedStatus(status) && autoFollow.value && !isSelectingText()) historyOpen.value=false
  else if(status==='running' || status==='stopping') historyOpen.value=true
})
watch(()=>[steps.value.length,activeStep.value?.id,latestStep.value?.id,latestStep.value?.status], async ()=>{
  if(autoFollow.value && !isSelectingText()){
    await nextTick()
    scrollToLatest()
  }else if(!autoFollow.value){
    showLatest.value=true
  }
},{flush:'post'})
</script>

<template>
  <section v-if='run?.run_id || connectionError' class='progress'>
    <div class='head'>
      <strong role="status" aria-live="polite">{{ currentOperation }}</strong>
      <span>{{ overallStatus }} · 已用 {{ elapsedText }}</span>
      <button v-if='run?.busy' type='button' :disabled='run.stop_requested' @click='emitStop'>{{ run.stop_requested ? '等待当前调用收尾…' : '停止本轮' }}</button>
      <button type='button' @click='emitSync'>核对任务状态</button>
    </div>
    <p v-if='run?.run_id' class='meta'>
      第 {{ run.turn ?? 0 }} 轮 · 工具 {{ run.tool_count ?? steps.length }} 次
      <template v-if='countParts.length'> · {{ countParts.join(' · ') }}</template>
    </p>
    <p v-if='honestNotice' class='notice'>{{ honestNotice }}</p>
    <p v-if='connectionError' class='error'>{{ connectionError }}</p>

    <div v-if='latestStep' class='latest' aria-live='polite'>
      <div class='latest-line'>
        <strong>{{ toolLabel(latestStep.name) }}</strong>
        <span>{{ statusLabel(latestStep.status) }}<template v-if='stepTime(latestStep)'> · {{ stepTime(latestStep) }}</template></span>
      </div>
      <p v-if='latestNotice' class='latest-note'>{{ latestNotice }}</p>
    </div>
    <div v-if='showLatest' class='jump-row'>
      <button type='button' class='jump' @click='scrollToLatest' aria-label='跳到最新步骤'>最新步骤 ↓</button>
    </div>

    <div v-if='steps.length' class='history-head'>
      <button type='button' class='toggle' :aria-expanded='historyOpen' @click='toggleHistory'>{{ historyLabel }}</button>
      <span v-if='!historyOpen && failureSteps.length' class='history-hint'>失败、跳过和有提示的步骤保持可见</span>
    </div>
    <ol v-if='historySteps.length' ref='historyEl' class='history' tabindex='0' @scroll='onHistoryScroll' @keydown='onHistoryKeydown' @selectstart='onSelectStart' @mouseup='onSelectEnd' @touchend='onSelectEnd' aria-label='工具步骤历史'>
      <li v-for='step in historySteps' :key='step.id' :data-status='step.status'>
        <div class='step-line'>
          <span>{{ toolLabel(step.name) }}</span>
          <span>{{ statusLabel(step.status) }}<template v-if='stepTime(step)'> · {{ stepTime(step) }}</template></span>
        </div>
        <small v-if='stepNotice(step)'>{{ stepNotice(step) }}</small>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.progress { border:1px solid var(--c-border);background:var(--c-surface-2);border-radius:8px;padding:12px;margin-bottom:12px; }
.head { display:flex;gap:10px;align-items:center;flex-wrap:wrap; }
.head strong { flex:1 1 220px;line-height:1.35; }
.head span,.meta,.notice,.latest-note,.history-hint { font-size:12px;color:var(--c-sub); }
.head span { white-space:nowrap; }
button { background:var(--c-surface);color:var(--c-ink);border:1px solid var(--c-border);border-radius:5px;padding:6px 10px;min-height:32px;cursor:pointer; }
button:disabled { opacity:.6;cursor:default; }
button:focus-visible,.history:focus-visible { outline:2px solid var(--c-accent, #7aa2ff);outline-offset:2px; }
.meta,.notice { margin:8px 0 0; }
.error { color:#ffb3c1; }
.latest { margin-top:10px;padding:8px 10px;border:1px solid var(--c-border);border-radius:6px;background:var(--c-surface); }
.latest-line { display:flex;justify-content:space-between;gap:12px;align-items:baseline;font-size:12px; }
.latest-line strong { font-size:13px; }
.latest-line span { color:var(--c-sub);white-space:nowrap; }
.latest-note { margin:4px 0 0; }
.jump-row { display:flex;justify-content:flex-end;margin-top:6px; }
.jump,.toggle { font-size:12px; }
.history-head { display:flex;gap:8px;align-items:center;margin-top:8px;flex-wrap:wrap; }
.history { padding:0;list-style:none;max-height:220px;overflow:auto;margin:6px 0 0;overscroll-behavior:contain;touch-action:pan-y;scrollbar-gutter:stable; }
.history li { padding:6px 0;border-top:1px solid var(--c-border);font-size:12px; }
.step-line { display:flex;justify-content:space-between;gap:12px; }
.step-line span:last-child { color:var(--c-sub);white-space:nowrap; }
.history small { display:block;margin-top:2px;color:var(--c-sub); }
.history li[data-status='failed'],.history li[data-status='partial'],.history li[data-status='cancelled'] { color:#ffb3c1; }
@media (max-width:560px){ .head strong{flex-basis:100%} .latest-line,.step-line{flex-direction:column;gap:2px} .head button{flex:1 1 auto;} }
</style>
