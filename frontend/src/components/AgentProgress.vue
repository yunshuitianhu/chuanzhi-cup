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
  submit_analysis_cards:'提交分析卡片',
  get_project_data_catalog:'读取数据目录',
  read_project_data:'读取业务数据',
  inspect_visual_asset:'读取画面'
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
  if(isFinishedStatus(props.run?.status)) return null
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
const toolCount=computed(()=>{
  const count=Number(props.run?.tool_count)
  const server=Number.isFinite(count) && count>=0 ? count : 0
  return Math.max(server,steps.value.length)
})
const stepsTruncated=computed(()=>toolCount.value > steps.value.length)
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
const finished=computed(()=>isFinishedStatus(props.run?.status))
const stepMeta=computed(()=>{
  const attempts=new Map(), ordered=[]
  for(const step of steps.value){
    const attempt=(attempts.get(step.name) || 0)+1
    attempts.set(step.name,attempt)
    ordered.push({step,attempt})
  }
  return ordered.map((item,index)=>{
    const recovered=item.step.status==='failed' && !!item.step.operation_id && ordered.slice(index+1).some(later=>
      later.step.id===item.step.recovered_by && later.step.operation_id===item.step.operation_id &&
      later.step.name===item.step.name && later.step.status==='succeeded')
    return {step:item.step,attempt:item.attempt,total:attempts.get(item.step.name),recovered}
  })
})
const unresolvedFailures=computed(()=>stepMeta.value.filter(item=>item.step.status==='failed' && !item.recovered))
const recoveredFailures=computed(()=>stepMeta.value.filter(item=>item.step.status==='failed' && item.recovered))
const reportAttempts=computed(()=>stepMeta.value.filter(item=>item.step.name==='generate_final_report'))
const reportOutcome=computed(()=>{
  const attempts=reportAttempts.value
  const failures=attempts.filter(item=>item.step.status==='failed')
  const last=attempts[attempts.length-1]
  const recovered=failures.filter(item=>item.recovered).length
  const server=props.run?.report_result
  const serverValid=server && typeof server.step_id==='string' && Number.isInteger(server.attempts) && server.attempts>0 &&
    Number.isInteger(server.analysis_revision) && server.analysis_revision===props.run.analysis_revision &&
    ['running','succeeded','failed','cancelled','skipped'].includes(server.status)
  if(!last && !serverValid) return {kind:stepsTruncated.value?'unknown':'none',
    label:stepsTruncated.value?'报告状态暂无法核实（较早记录未保留）':'未生成报告',attempts,failures:0,recovered:0}
  const status=serverValid?server.status:last.step.status
  const totalAttempts=serverValid?server.attempts:attempts.length
  const base={attempts,failures:failures.length,recovered,totalAttempts}
  if(status==='running') return {kind:'running',label:'报告生成中',...base}
  if(status==='succeeded') return {kind:'generated',label:'报告已生成',...base}
  if(status==='cancelled') return {kind:'cancelled',label:'报告未完成（已停止）',...base}
  if(status==='skipped') return {kind:'skipped',label:'报告已跳过',...base}
  if(attempts.some(item=>item.step.status==='succeeded'))
    return {kind:'stale',label:'报告未更新（最新一次失败）',...base}
  return {kind:'failed',label:'报告未生成',...base}
})
const showReport=computed(()=>!!props.run?.auto || !!props.run?.report_result || reportAttempts.value.length>0)
const otherRecovered=computed(()=>recoveredFailures.value.filter(item=>item.step.name!=='generate_final_report').length)
const summaryParts=computed(()=>{
  if(!props.run?.run_id) return []
  const parts=[]
  const report=reportOutcome.value
  if(showReport.value && (finished.value || report.attempts.length)) parts.push(report.label)
  if(report.kind==='generated' && report.recovered)
    parts.push(`${report.recovered}次报告失败后重试成功`)
  if(otherRecovered.value)
    parts.push(`${otherRecovered.value}次失败已由同一请求的重试恢复`)
  if(unresolvedFailures.value.length)
    parts.push(`未恢复失败 ${unresolvedFailures.value.length}`)
  if(!finished.value && countParts.value.length) parts.push(countParts.value.join(' · '))
  parts.push(`${toolCount.value} 次工具调用`)
  return parts
})
const honestNotice=computed(()=>{
  const run=props.run
  if(!run) return ''
  if(run.busy && (run.stop_requested || run.status==='stopping')) return safeText(run.notice,120) || '停止请求会阻止后续步骤；当前调用收尾前操作仍保持锁定。'
  if(run.status==='failed') return '本轮执行失败；已完成的步骤仍保留，失败原因见下方记录。'
  if(run.status==='partial') return '本轮部分完成；未恢复的失败或跳过项见下方记录。'
  if(run.status==='cancelled') return '本轮已停止；已完成的部分保留。'
  if(unresolvedFailures.value.length) return `存在 ${unresolvedFailures.value.length} 次未恢复失败；仅核实到同一请求的重试成功才标记恢复。`
  if(recoveredFailures.value.length) return `曾有 ${recoveredFailures.value.length} 次失败，已核实后续重试成功；下方保留每次尝试。`
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
const detailsOpen=ref(false)
const detailsRef=ref(null)
const detailsButton=ref(null)
let returnFocus=null
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
const detailsLabel=computed(()=>detailsOpen.value ? '收起详情'
  : stepsTruncated.value ? `查看详情（最近${steps.value.length}次）` : `查看详情（${toolCount.value}）`)
const openDetails=async()=>{
  try{returnFocus=globalThis.document?.activeElement || null}catch{returnFocus=null}
  detailsOpen.value=true
  await nextTick()
  if(autoFollow.value && !isSelectingText()) scrollToLatest()
  detailsRef.value?.focus?.()
}
const closeDetails=async()=>{
  if(!detailsOpen.value) return
  detailsOpen.value=false
  await nextTick()
  const target=returnFocus?.focus ? returnFocus : detailsButton.value
  target?.focus?.()
}
const toggleDetails=()=>detailsOpen.value ? closeDetails() : openDetails()
const FOCUSABLE='button:not(:disabled),[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex]:not([tabindex="-1"])'
const onDetailsKeydown=event=>{
  if(!event) return
  if(event.key==='Escape'){
    event.preventDefault?.()
    closeDetails()
    return
  }
  if(event.key!=='Tab') return
  const root=detailsRef.value
  const items=root?.querySelectorAll ? Array.from(root.querySelectorAll(FOCUSABLE)).filter(el=>!el.disabled) : []
  if(!items.length){
    event.preventDefault?.()
    return
  }
  const first=items[0], last=items[items.length-1]
  const active=globalThis.document?.activeElement
  if(event.shiftKey && (active===first || active===root)){
    event.preventDefault?.()
    last.focus?.()
  }else if(!event.shiftKey && active===last){
    event.preventDefault?.()
    first.focus?.()
  }
}
onMounted(()=>{
  globalThis.document?.addEventListener('pointerup',onSelectEnd)
  globalThis.document?.addEventListener('pointercancel',onSelectEnd)
  nextTick(()=>{
    if(detailsOpen.value && autoFollow.value && !isSelectingText()) scrollToLatest()
  })
})
onBeforeUnmount(()=>{
  globalThis.document?.removeEventListener('pointerup',onSelectEnd)
  globalThis.document?.removeEventListener('pointercancel',onSelectEnd)
})
watch(()=>props.run?.run_id, async (id, old)=>{
  if(id===old) return
  detailsOpen.value=false
  autoFollow.value=true
  showLatest.value=false
  await nextTick()
},{flush:'post'})
watch(()=>[steps.value.length,activeStep.value?.id,latestStep.value?.id,latestStep.value?.status], async ()=>{
  if(!detailsOpen.value) return
  if(autoFollow.value && !isSelectingText()){
    await nextTick()
    scrollToLatest()
  }else if(!autoFollow.value){
    showLatest.value=true
  }
},{flush:'post'})
</script>

<template>
  <div class='agent-progress'>
  <section v-if='run?.run_id || connectionError' class='progress' :data-status='run?.status || "unknown"'>
    <div class='head'>
      <span class='status-dot' aria-hidden='true'></span>
      <strong role='status' aria-live='polite'>{{ currentOperation }}</strong>
      <span v-if='run?.run_id' class='timing'>{{ run?.busy ? `${overallStatus} · 已用 ${elapsedText}` : `已用 ${elapsedText}` }}</span>
      <div class='head-actions'>
        <button v-if='run?.busy' type='button' :disabled='run.stop_requested' @click='emitStop'>{{ run.stop_requested ? '等待当前调用收尾…' : '停止本轮' }}</button>
        <button v-if='run?.run_id' ref='detailsButton' type='button' class='toggle' :aria-expanded='detailsOpen' aria-controls='agent-progress-details' @click='toggleDetails'>{{ detailsLabel }}</button>
        <button type='button' class='sync' @click='emitSync'>核对任务状态</button>
      </div>
    </div>
    <p v-if='summaryParts.length' class='summary'>{{ summaryParts.join(' · ') }}</p>
    <p v-if='connectionError' class='error' role='alert'>{{ connectionError }}</p>
  </section>

  <div v-if='detailsOpen' class='details-layer' @keydown='onDetailsKeydown'>
    <div class='details-backdrop' aria-hidden='true' @click='closeDetails'></div>
    <section ref='detailsRef' id='agent-progress-details' class='details' role='dialog' aria-modal='true' aria-labelledby='agent-progress-title' tabindex='-1'>
      <header class='details-head'>
        <div class='details-title'>
          <strong id='agent-progress-title'>任务详情</strong>
          <p v-if='run?.run_id' class='detail-meta'>{{ overallStatus }} · 第 {{ run.turn ?? 0 }} 轮 · 工具 {{ toolCount }} 次 · 已用 {{ elapsedText }}</p>
          <p v-else class='detail-meta'>任务状态尚未核实</p>
        </div>
        <button type='button' class='close' @click='closeDetails'>关闭</button>
      </header>
      <p v-if='honestNotice' class='notice'>{{ honestNotice }}</p>
      <p v-if='connectionError' class='error' role='alert'>{{ connectionError }}</p>

      <div v-if='run?.run_id' class='counts-block'>
        <p class='detail-meta'>尝试级统计：{{ countParts.length ? countParts.join(' · ') : '暂无步骤' }}<template v-if='unresolvedFailures.length'> · 未恢复失败 {{ unresolvedFailures.length }}</template><template v-else-if='recoveredFailures.length'> · 已核实恢复 {{ recoveredFailures.length }}</template></p>
        <p class='detail-meta'>同一工具多次调用按次保留，不合并为多个独立任务。</p>
        <p v-if='stepsTruncated' class='detail-meta'>服务端只保留最近 {{ steps.length }} 次步骤；更早尝试不在本页展示，工具总数仍为 {{ toolCount }}。</p>
      </div>

      <div v-if='showReport' class='report-card' tabindex='0' aria-label='报告状态与尝试记录' :data-status='reportOutcome.kind'>
        <div class='report-head'><strong>报告状态</strong><span>{{ reportOutcome.label }}</span></div>
        <ol v-if='reportOutcome.attempts.length' class='report-attempts'>
          <li v-for='item in reportOutcome.attempts' :key='item.step.id' :data-status='item.step.status'>
            <span>第 {{ item.attempt }} 次</span>
            <span>{{ statusLabel(item.step.status) }}<template v-if='stepTime(item.step)'> · {{ stepTime(item.step) }}</template></span>
            <small v-if='stepNotice(item.step)'>{{ stepNotice(item.step) }}</small>
            <small v-if='item.recovered' class='relation'>后续报告调用已成功</small>
          </li>
        </ol>
        <p v-else class='detail-meta'>{{reportOutcome.kind==='unknown'?'较早的报告调用记录未保留，暂无法核实。':reportOutcome.totalAttempts?'报告状态来自服务端本轮记录；较早尝试不在最近步骤中。':'本轮尚未调用报告生成。'}}</p>
      </div>

      <div v-if='latestStep' class='latest' aria-live='polite'>
        <div class='latest-line'>
          <strong>{{ toolLabel(latestStep.name) }}</strong>
          <span>{{ statusLabel(latestStep.status) }}<template v-if='stepTime(latestStep)'> · {{ stepTime(latestStep) }}</template></span>
        </div>
        <p v-if='latestNotice' class='latest-note'>{{ latestNotice }}</p>
      </div>

      <div v-if='steps.length' class='history-head'>
        <strong class='history-title'>{{ stepsTruncated ? '最近工具尝试' : '全部工具尝试' }}（{{ steps.length }}<template v-if='stepsTruncated'> / {{ toolCount }}</template>）</strong>
        <span class='history-hint'>按发生顺序保留，每次调用单独记录</span>
      </div>
      <ol v-if='steps.length' ref='historyEl' class='history' tabindex='0' @scroll='onHistoryScroll' @keydown='onHistoryKeydown' @selectstart='onSelectStart' @mouseup='onSelectEnd' @touchend='onSelectEnd' aria-label='工具步骤历史'>
        <li v-for='item in stepMeta' :key='item.step.id' :data-status='item.step.status'>
          <div class='step-line'>
            <span><span v-if='item.total>1' class='attempt'>第{{ item.attempt }}次</span> {{ toolLabel(item.step.name) }}</span>
            <span>{{ statusLabel(item.step.status) }}<template v-if='stepTime(item.step)'> · {{ stepTime(item.step) }}</template></span>
          </div>
          <small v-if='item.recovered' class='relation'>{{ item.step.name==='generate_final_report' ? '后续报告调用已成功' : '后续同一请求重试已成功' }}</small>
          <small v-if='stepNotice(item.step)'>{{ stepNotice(item.step) }}</small>
        </li>
      </ol>
      <p v-else class='detail-meta'>本轮尚无工具步骤。</p>

      <div v-if='showLatest' class='jump-row'>
        <button type='button' class='jump' @click='scrollToLatest' aria-label='跳到最新步骤'>最新步骤 ↓</button>
      </div>
    </section>
  </div>
  </div>
</template>

<style scoped>
.agent-progress { display:contents; }
.progress { border:1px solid var(--c-border);background:var(--c-surface-2);border-radius:8px;padding:8px 12px;margin-bottom:10px;flex-shrink:0; }
.head { display:flex;gap:6px 10px;align-items:center;flex-wrap:wrap; }
.status-dot { width:8px;height:8px;border-radius:50%;background:var(--c-sub);flex:0 0 auto; }
.progress[data-status='running'] .status-dot { background:var(--c-accent);box-shadow:0 0 0 3px #7cd4eb22; }
.progress[data-status='succeeded'] .status-dot { background:#73d5be; }
.progress[data-status='partial'] .status-dot,.progress[data-status='failed'] .status-dot { background:var(--sev-mild,#e6a23c); }
.progress[data-status='cancelled'] .status-dot { background:var(--c-muted); }
.head strong { flex:1 1 220px;min-width:0;line-height:1.35;font-size: 15px;overflow-wrap:anywhere; }
.timing,.summary,.notice,.detail-meta,.latest-note,.history-hint,.report-attempts small { font-size: 14px;color:var(--c-sub); }
.timing { white-space:nowrap; }
.head-actions { display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-left:auto; }
button { background:var(--c-surface);color:var(--c-ink);border:1px solid var(--c-border);border-radius:5px;padding:5px 9px;min-height:30px;font-size: 14px;cursor:pointer; }
button:disabled { opacity:.6;cursor:default; }
button:focus-visible,.history:focus-visible,.details:focus-visible { outline:2px solid var(--c-accent, #7aa2ff);outline-offset:2px; }
.summary { margin:2px 0 0;line-height:1.5;overflow-wrap:anywhere; }
.error { color:#ffb3c1;margin:6px 0 0;font-size: 14px; }
.details-layer { position:fixed;inset:0;z-index:80;display:flex;justify-content:flex-end;padding:16px;padding-top:max(16px,env(safe-area-inset-top));padding-bottom:max(16px,env(safe-area-inset-bottom)); }
.details-backdrop { position:absolute;inset:0;background:rgba(4,10,16,.62); }
.details { position:relative;width:min(560px,100%);max-height:100%;display:flex;flex-direction:column;gap:9px;background:var(--c-surface);border:1px solid var(--c-border);border-radius:12px;padding:14px 16px;box-shadow:0 20px 60px rgba(0,0,0,.45);overflow:auto;overscroll-behavior:contain; }
.details-head { position:sticky;top:0;z-index:1;background:var(--c-surface);display:flex;gap:12px;align-items:flex-start;justify-content:space-between;flex-shrink:0; }
.details-title { min-width:0; }
.details-head strong { font-size: 16px; }
.detail-meta { margin:4px 0 0;line-height:1.6;overflow-wrap:anywhere; }
.notice { margin:0;line-height:1.6; }
.counts-block { flex-shrink:0;padding:7px 9px;border:1px solid var(--c-border);border-radius:7px;background:var(--c-surface-2); }
.report-card { flex-shrink:0;max-height:30%;overflow:auto;padding:8px 10px;border:1px solid var(--c-border);border-radius:8px;background:var(--c-surface-2); }
.report-card[data-status='generated'] { border-color:#2d635a; }
.report-card[data-status='failed'],.report-card[data-status='stale'] { border-color:#794354; }
.report-head { display:flex;justify-content:space-between;gap:10px;font-size: 14px; }
.report-attempts { margin:6px 0 0;padding:0;list-style:none;font-size: 14px; }
.report-attempts li { display:flex;flex-wrap:wrap;justify-content:space-between;gap:2px 8px;padding:4px 0;border-top:1px solid var(--c-border); }
.report-attempts li small { flex-basis:100%;line-height:1.5; }
.report-attempts li[data-status='failed'] { color:#ffb3c1; }
.latest { flex-shrink:0;max-height:20%;overflow:auto;padding:7px 9px;border:1px solid var(--c-border);border-radius:7px;background:var(--c-surface-2); }
.latest-line { display:flex;justify-content:space-between;gap:12px;align-items:baseline;font-size: 14px; }
.latest-line strong { font-size: 15px; }
.latest-line span { color:var(--c-sub);white-space:nowrap; }
.latest-note { margin:4px 0 0; }
.jump-row { display:flex;justify-content:flex-end;flex-shrink:0; }
.jump,.toggle { font-size: 14px; }
.history-head { display:flex;gap:8px;align-items:baseline;flex-wrap:wrap;flex-shrink:0; }
.history-title { font-size: 15px; }
.history { flex:1;min-height:120px;padding:0;list-style:none;overflow:auto;margin:0;overscroll-behavior:contain;touch-action:pan-y;scrollbar-gutter:stable; }
.history li { padding:6px 0;border-top:1px solid var(--c-border);font-size: 14px; }
.step-line { display:flex;justify-content:space-between;gap:12px; }
.step-line span:last-child { color:var(--c-sub);white-space:nowrap; }
.attempt { display:inline-block;margin-right:4px;padding:0 4px;border-radius:3px;background:var(--c-surface);color:var(--c-sub);font-size: 14px; }
.history small { display:block;margin-top:2px;color:var(--c-sub);line-height:1.5; }
.history small.relation { color:#9fd8c8; }
.history li[data-status='failed'],.history li[data-status='partial'],.history li[data-status='cancelled'] { color:#ffb3c1; }
@media (max-width:600px){
  .head strong{flex-basis:auto}
  .head-actions{width:100%;margin-left:0}
  .head-actions button{flex:1 1 auto}
  .timing{font-size: 14px}
  .latest-line,.step-line{flex-direction:column;gap:2px}
  .details-layer{padding:0;align-items:flex-end}
  .details{width:100%;max-height:84dvh;border-radius:14px 14px 0 0;border-bottom:0;padding:13px}
  .report-card{max-height:26%}
  .history{min-height:100px}
}
</style>
