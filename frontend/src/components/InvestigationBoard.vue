<script setup>
import {ref,toRaw,watch,onMounted,onBeforeUnmount} from 'vue'
import {api} from '../api/client'
import {useSessionStore} from '../stores/session'

const store=useSessionStore()
const board=ref(null),selected=ref(''),draft=ref(null),dirty=ref(false),reviewAck=ref(false)
const loading=ref(false),saving=ref(false),error=ref(''),message=ref('')
let version=0,baseScope='',baseRevision=0
const labels={open:'待排查',planned:'已安排复测',sampled:'已记录复测',reviewed:'已人工复核',closed:'已结束本轮排查',needs_recheck:'依据已变化，需重新复核'}
const indicators={ammonia:'氨氮',do:'溶解氧',cod:'COD',ph:'pH',turbidity:'浊度',sd:'透明度',orp:'ORP',flow:'流量/流向',connection:'入河连接'}
function emptyDraft() {
  return {title:'',hypothesis:'',supporting_notes:'',opposing_notes:'',status:'open',
    retest:{location:'',latitude:null,longitude:null,indicators:[],plan:'',observed_at:'',result:''},
    review:{outcome:'',reviewer:'',notes:''}}
}
function edit(event) {
  dirty.value=true
  if(event.target?.name!=='review-ack')reviewAck.value=false
}
function selectItem(id,force=false) {
  if(dirty.value && !force) {error.value='草稿尚未保存，请先保存，或点击“放弃草稿并重新载入”。';return}
  selected.value=id
  const item=board.value?.items.find(item=>item.id===id)
  draft.value=item?structuredClone(toRaw(item.draft)):emptyDraft()
  dirty.value=false;reviewAck.value=false;error.value='';message.value=''
  baseScope=board.value?.scope_id||'';baseRevision=board.value?.revision||0
}
async function load() {
  const token=++version,workspace=store.workspaceRevision
  if(!store.ready || store.chatRunning)return
  loading.value=true
  try {
    const result=await api.investigations()
    if(token!==version || workspace!==store.workspaceRevision)return
    board.value=result
    if(dirty.value) {message.value='草稿保留。数据或任务版本可能已变化，重新载入后再核对保存。';return}
    const preferred=store.selectedInvestigationId || selected.value
    selectItem(result.items.some(item=>item.id===preferred)?preferred:(result.items[0]?.id||''),true)
  } catch(e){if(token===version)error.value=e.message}
  finally{if(token===version)loading.value=false}
}
async function save() {
  if(saving.value || !draft.value || !board.value)return
  const workspace=store.workspaceRevision, token=++version
  saving.value=true;error.value='';message.value=''
  try {
    const body=structuredClone(toRaw(draft.value))
    for(const key of ['latitude','longitude'])body.retest[key]=body.retest[key]===''||body.retest[key]===null?null:Number(body.retest[key])
    const result=await store.saveInvestigation(selected.value,{scope_id:baseScope,expected_revision:baseRevision,
      draft:body,...(selected.value?{acknowledge_review:reviewAck.value}:{})})
    if(token!==version || workspace!==store.workspaceRevision)return
    board.value=result.board;dirty.value=false;selectItem(result.task_id,true)
    message.value=result.board.persisted_project?'排查记录已写入当前项目，可在重启后恢复。':'已更新当前临时会话；请在左侧另存为项目以便重启恢复。'
    if(result.refresh_warning)message.value+=' '+result.refresh_warning
  } catch(e){if(token===version)error.value=e.message}
  finally{saving.value=false}
}
watch(()=>[store.workspaceRevision,store.status.analysis_revision,store.status.results_revision,
  store.investigationRevision,store.chatRunning,store.ready],()=>{if(!saving.value)load()})
onMounted(load)
onBeforeUnmount(()=>{version++})
</script>

<template>
  <section id="investigation-board" class="investigation-board">
    <p class="scope">候选假设 → 记录支持/反对依据 → 安排复测 → 人工复核。结束排查不代表确认污染源或责任。</p>
    <p v-if="board && !board.persisted_project" class="notice">临时会话：请在左侧另存为项目。已保存项目的排查修改会自动写入项目版本。</p>
    <p v-if="loading" role="status">正在读取排查任务…</p>
    <p v-if="store.chatRunning">分析执行中，结束后可更新排查任务。</p>
    <p v-if="error" role="alert" class="error">{{error}}</p>
    <p v-if="message" role="status" class="notice">{{message}}</p>
    <div class="actions">
      <button :disabled="loading || saving || !board || store.chatRunning" @click="selectItem('')">新建排查任务</button>
      <button :disabled="loading || saving || store.chatRunning" @click="load">刷新任务</button>
      <button v-if="dirty" :disabled="saving" @click="selectItem(selected,true)">放弃草稿并重新载入</button>
    </div>
    <p v-if="board && !board.items.length">尚无排查任务。可手工创建，也可从报告证据的采样记录加入。</p>
    <div class="task-list">
      <button v-for="item in board?.items||[]" :key="item.id" :class="{selected:selected===item.id}" :disabled="saving || store.chatRunning" @click="selectItem(item.id)">
        <strong>{{item.draft.title}}</strong><span>{{labels[item.effective_status]}}</span>
        <small>{{item.origin.filename||'尚未加载监测表'}} · {{item.basis_status==='stale'?'原依据已变化':'依据版本匹配'}}</small>
      </button>
    </div>
    <form v-if="draft" @submit.prevent="save" @input="edit" @change="dirty=true">
      <fieldset :disabled="saving || store.loading || store.chatRunning">
        <label>排查标题<input v-model="draft.title" maxlength="120" required /></label>
        <label>候选假设<textarea v-model="draft.hypothesis" maxlength="2000" rows="2" required /></label>
        <div class="two">
          <label>支持依据（人工记录）<textarea v-model="draft.supporting_notes" maxlength="4000" rows="3" /></label>
          <label>反对或矛盾依据（人工记录）<textarea v-model="draft.opposing_notes" maxlength="4000" rows="3" /></label>
        </div>
        <details v-if="selected && board?.items.find(item=>item.id===selected)?.reference">
          <summary>创建时关联的报告采样记录</summary>
          <template v-for="item in board.items.filter(item=>item.id===selected)" :key="item.id">
            <p>{{item.reference_status==='current'?'当前报告关联可核对':'历史记录快照；报告已变化或不在当前会话，不关联当前地图。'}}</p>
            <pre>{{item.reference.excerpt}}</pre>
          </template>
        </details>
        <h3>复测计划与实际记录</h3>
        <label>复测位置说明<input v-model="draft.retest.location" maxlength="300" placeholder="候选位置和上下游对照位置" /></label>
        <div class="two"><label>纬度 WGS84<input v-model="draft.retest.latitude" type="number" step="any" min="-90" max="90" /></label><label>经度 WGS84<input v-model="draft.retest.longitude" type="number" step="any" min="-180" max="180" /></label></div>
        <div class="checks" role="group" aria-label="复测指标"><label v-for="(label,key) in indicators" :key="key"><input v-model="draft.retest.indicators" type="checkbox" :value="key" />{{label}}</label></div>
        <label>复测计划<textarea v-model="draft.retest.plan" maxlength="3000" rows="2" /></label>
        <label>实际复测时间（须含时区）<input v-model="draft.retest.observed_at" maxlength="50" placeholder="例如 2026-10-22T14:00:00+08:00" /></label>
        <label>实际复测记录<textarea v-model="draft.retest.result" maxlength="4000" rows="3" placeholder="记录方法、读数、对照结果及资料来源；没有复测时不要填写已完成" /></label>
        <h3>人工复核</h3>
        <div class="two"><label>复核人<input v-model="draft.review.reviewer" maxlength="100" /></label><label>本次假设复核结果<select v-model="draft.review.outcome" aria-label="本次假设复核结果"><option value="">尚未复核</option><option value="supported">本次记录支持假设</option><option value="not_supported">本次记录不支持假设</option><option value="inconclusive">证据不足，暂无法区分</option></select></label></div>
        <label>复核意见与剩余缺口<textarea v-model="draft.review.notes" maxlength="4000" rows="3" /></label>
        <label>排查状态<select v-model="draft.status" aria-label="排查状态"><option v-for="(label,key) in labels" v-show="key!=='needs_recheck'" :key="key" :value="key" :disabled="key==='needs_recheck' || (!selected && key!=='open')">{{label}}</option></select></label>
        <label v-if="['reviewed','closed'].includes(draft.status)" class="ack"><input v-model="reviewAck" name="review-ack" type="checkbox" />我已人工复核本次记录；这不代表确认污染责任。</label>
        <button class="primary" type="submit" :disabled="loading || !board || (!dirty && !!selected)">{{saving?'保存中…':selected?'保存排查记录':'创建排查任务'}}</button>
      </fieldset>
    </form>
    <details v-if="selected && board?.items.find(item=>item.id===selected)?.history.length"><summary>查看最近修改历史</summary><pre>{{JSON.stringify(board.items.find(item=>item.id===selected).history,null,2)}}</pre></details>
  </section>
</template>

<style scoped>
.investigation-board{font-size:13px;line-height:1.7;scroll-margin-top:90px}.scope,.notice,small{color:var(--c-sub)}.error{color:#ffba8c}.actions,.checks{display:flex;flex-wrap:wrap;gap:8px}.task-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:8px;margin:14px 0}.task-list button{text-align:left;display:flex;flex-direction:column}.selected{outline:1px solid var(--c-accent)}
fieldset{border:0;padding:0;min-width:0}label{display:flex;flex-direction:column;gap:4px;margin:8px 0;color:var(--c-sub)}input,textarea,select{background:var(--c-bg);color:var(--c-ink);border:1px solid var(--c-border);border-radius:6px;padding:8px;font:inherit;min-width:0;max-width:100%;box-sizing:border-box}textarea{resize:vertical}.two{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.checks label,.ack{flex-direction:row;align-items:center}button{font:inherit;padding:7px 12px;border:1px solid var(--c-border);border-radius:7px;background:var(--c-surface-2);color:var(--c-ink);cursor:pointer}button:disabled{opacity:.5;cursor:default}.primary{background:var(--c-accent);color:#06121c;margin:12px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:260px;overflow:auto;background:var(--c-bg);padding:10px}h3{font-size:14px;margin-top:18px}@media(max-width:650px){.two{grid-template-columns:1fr}}
</style>
