<script setup>
import {ref, watch, onBeforeUnmount} from 'vue'
import {RouterLink} from 'vue-router'
import {api} from '../api/client'
import {useSessionStore} from '../stores/session'
import {numberText} from '../utils/mapInteraction'

const props=defineProps({id:{type:String,required:true}})
const store=useSessionStore()
const expanded=ref(false), snapshot=ref(null), rows=ref(null), tool=ref(null)
const busy=ref(false), error=ref(''), selected=ref(null)
let version=0
const names={get_data_summary:'数据统计',find_black_spots:'黑臭筛查',get_comprehensive_summary:'综合摘要',
  search_treatment_cases:'治理案例检索',analyze_video:'视频线索',cross_validate_video:'交叉印证',
  get_national_station_data:'国控站点背景',reverse_geocode:'地址查询',search_nearby_pollution_sources:'周边排查线索'}
const statuses={succeeded:'完成',failed:'失败',skipped:'跳过'}
async function request(action, apply) {
  const token=++version, workspace=store.workspaceRevision
  busy.value=true;error.value=''
  try {
    const result=await action()
    if(token===version && workspace===store.workspaceRevision)apply(result)
  } catch(e) {
    if(token===version && workspace===store.workspaceRevision) {
      error.value=e.message
      if(e.status===409) {snapshot.value=null;rows.value=null;tool.value=null}
    }
  } finally {if(token===version)busy.value=false}
}
async function load() {
  rows.value=null;tool.value=null;selected.value=null;snapshot.value=null
  if(!expanded.value || store.chatRunning)return
  await request(()=>api.reportEvidence(props.id), result=>{snapshot.value=result})
}
async function showRows(fact,offset=0) {
  rows.value=null;tool.value=null;selected.value=fact
  await request(()=>api.evidenceRows(props.id,fact.id,offset), result=>{rows.value=result})
}
async function showTool(id) {
  rows.value=null;tool.value=null;selected.value=null
  await request(()=>api.evidenceTool(props.id,id), result=>{tool.value=result})
}
function toggle() {
  expanded.value=!expanded.value
  if(expanded.value)load()
  else {version++;busy.value=false}
}
watch(()=>[props.id,store.workspaceRevision,store.status.analysis_revision,
  store.status.results_revision,store.status.target_water_class,store.chatRunning],()=>{
  version++;busy.value=false;error.value='';snapshot.value=null;rows.value=null;tool.value=null
  if(expanded.value)load()
})
onBeforeUnmount(()=>{version++})
</script>

<template>
  <section class="report-evidence">
    <button class="evidence-toggle" :aria-expanded="expanded" @click="toggle">{{expanded?'收起':'查看'}}本轮数据与工具证据</button>
    <div v-if="expanded" class="evidence-body">
      <p v-if="store.chatRunning">任务执行中，结束后可核对证据。</p>
      <p v-if="busy" role="status">正在核对证据…</p>
      <p v-if="error" role="alert" class="warning">{{error}}</p>
      <template v-if="snapshot">
        <p :class="{warning:snapshot.status!=='current'}"><strong>{{snapshot.status==='current'?'当前快照匹配':'历史证据已过期'}}</strong> · {{snapshot.notice}}</p>
        <p class="sub">{{snapshot.filename || '未提供文件名'}} · 数据版本 {{snapshot.data_version}} · 目标 {{snapshot.target_water_class}} 类<br />证据快照创建于 {{snapshot.created_at}}</p>
        <p class="sub">{{snapshot.data_identity?.label}} · {{snapshot.scope}}</p>
        <div class="facts">
          <button v-for="fact in snapshot.facts" :key="fact.id" :disabled="busy || snapshot.status!=='current' || fact.record_count===null" @click="showRows(fact)">
            <span>{{fact.label}}</span><strong>{{fact.value??'—'}} {{fact.unit}}</strong>
            <small>{{fact.record_count===null?'旧检测缺少唯一行关联':`${fact.record_count} 条支持记录`}}</small>
          </button>
        </div>
        <p v-if="!snapshot.facts.length" class="sub">本轮未取得可核对的数据统计，不沿用历史统计生成卡片。</p>
        <p v-if="rows"><strong>{{selected?.label}}</strong> · 支持集合共 {{rows.total}} 条；当前 {{rows.total?rows.offset+1:0}}–{{Math.min(rows.offset+rows.limit,rows.total)}} 条。</p>
        <div v-if="rows" class="records">
          <table>
            <thead><tr><th>采样记录</th><th>时间</th><th>氨氮 mg/L</th><th>DO mg/L</th><th>定位</th></tr></thead>
            <tbody><tr v-for="row in rows.rows" :key="row.position">
              <td><details><summary>#{{row.record_number}}</summary><p>输入记录ID：{{row.input_record_id??'未保留'}}</p><p>{{row.quality?.label}}</p><pre>{{JSON.stringify({观测值:row.values,插值参考:row.interpolated,清洗前读数:row.quality?.raw_text},null,2)}}</pre></details></td>
              <td>{{row.time??'未提供'}}</td><td>{{numberText(row.values.ammonia_nitrogen)}}</td><td>{{numberText(row.values.dissolved_oxygen)}}</td>
              <td><RouterLink v-if="row.map_sample_id!==null" :to="{path:'/charts',query:{evidence:id,row:String(row.position)},hash:'#trace-map'}">地图查看</RouterLink><span v-else>无有效GPS</span></td>
            </tr></tbody>
          </table>
          <button :disabled="busy || rows.offset===0" @click="showRows(selected,Math.max(0,rows.offset-rows.limit))">上一页</button>
          <button :disabled="busy || rows.offset+rows.limit>=rows.total" @click="showRows(selected,rows.offset+rows.limit)">下一页</button>
        </div>
        <div class="tools">
          <strong>本轮工具返回</strong>
          <button v-for="item in snapshot.tools" :key="item.id" :disabled="busy" @click="showTool(item.id)">{{names[item.name]||item.name}} · {{statuses[item.status]||item.status}}</button>
        </div>
        <div v-if="tool" class="tool-output">
          <p>{{tool.notice}} <strong v-if="tool.status!=='current'">历史快照，已过期。</strong></p>
          <p class="sub">调用开始 {{tool.tool.started_at}}<br />调用结束 {{tool.tool.finished_at}} · 这不是监测时间。</p>
          <pre>{{tool.tool.arguments}}</pre>
          <pre>{{tool.tool.output}}</pre>
          <p v-if="tool.tool.truncated" class="warning">原返回 {{tool.tool.output_chars}} 字符；快照仅保存前30000字符，不将截断片段作为完整统计。</p>
        </div>
        <div v-if="snapshot.references.length" class="references">
          <strong>本轮知识库参考资料</strong>
          <p v-for="(item,i) in snapshot.references" :key="i">{{item.title}} · {{item.location}}<br /><small>{{item.notice}}</small> <button :disabled="busy" @click="showTool(item.tool_id)">查看检索返回</button></p>
        </div>
        <button :disabled="busy || store.chatRunning" @click="load">重新核对快照</button>
      </template>
    </div>
  </section>
</template>

<style scoped>
.report-evidence{margin-top:12px;border-top:1px solid var(--c-border);padding-top:10px;max-width:100%}
button{border:1px solid var(--c-border);background:var(--c-surface);color:var(--c-ink);border-radius:6px;padding:7px 10px;font:inherit;cursor:pointer}
button:disabled{opacity:.5;cursor:default}.evidence-toggle{color:var(--c-accent)}
.evidence-body{font-size:12px;line-height:1.7;overflow-wrap:anywhere}.sub,small{color:var(--c-sub)}.warning{color:#ffca78}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:7px;margin:12px 0}
.facts button{text-align:left;display:flex;flex-direction:column;gap:3px}.facts strong{font-size:14px}.facts span{color:var(--c-sub)}
.records{overflow-x:auto;max-width:100%}table{border-collapse:collapse;min-width:440px;width:100%;font-size:12px}th,td{padding:7px;border-bottom:1px solid var(--c-border);text-align:left;vertical-align:top}td:nth-child(2){min-width:115px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--c-bg);padding:8px;border-radius:6px;max-height:300px;overflow:auto;font-size:11px}
.tools{display:flex;gap:6px;flex-wrap:wrap;margin:14px 0}.tools strong{width:100%}.references{margin:14px 0}a{color:var(--c-accent)}
</style>
