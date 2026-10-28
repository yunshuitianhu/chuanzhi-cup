<script setup>
import {ref,watch,onBeforeUnmount,nextTick} from 'vue'
import {useRoute} from 'vue-router'
import {useSessionStore} from '../stores/session'
import {api} from '../api/client'
import {numberText} from '../utils/mapInteraction'
const emit=defineEmits(['focus'])
const route=useRoute(), store=useSessionStore()
const row=ref(null), error=ref(''), loading=ref(false)
let version=0
watch(()=>[route.query.evidence,route.query.row,store.workspaceRevision,
  store.status.analysis_revision,store.status.results_revision,store.status.target_water_class,store.chatRunning],async()=>{
  const token=++version
  emit('focus',null);row.value=null;error.value='';loading.value=false
  const id=route.query.evidence, position=route.query.row
  if(id===undefined && position===undefined)return
  if(typeof id!=='string' || !/^[a-zA-Z0-9-]{1,64}$/.test(id) || typeof position!=='string' || !/^\d{1,9}$/.test(position)) {
    error.value='报告定位参数无效，请从报告证据入口重新选择。';return
  }
  if(store.chatRunning){error.value='任务执行中，结束后将重新核对定位证据。';return}
  loading.value=true
  try {
    const result=await api.evidenceRecord(id,Number(position))
    if(token!==version)return
    row.value=result;emit('focus',result.map_sample_id)
    await nextTick()
    if(token===version && typeof document!=='undefined')document.getElementById('trace-map')?.scrollIntoView({block:'start'})
  } catch(e) {if(token===version)error.value=`无法关联报告记录：${e.message}`}
  finally {if(token===version)loading.value=false}
},{immediate:true})
onBeforeUnmount(()=>{version++})
</script>

<template>
  <div v-if="loading || error || row" class="evidence-focus" role="status">
    <span v-if="loading">正在核对报告的采样记录…</span>
    <span v-else-if="error">{{error}}</span>
    <span v-else>报告关联记录 #{{row.record_number}} · {{row.time??'未提供时间'}} · 氨氮 {{numberText(row.values.ammonia_nitrogen)}} mg/L · DO {{numberText(row.values.dissolved_oxygen)}} mg/L。{{row.map_sample_id===null?'无有效GPS，无法在地图定位。':'地图蓝色标记为该原始采样位置。'}}</span>
  </div>
</template>
<style scoped>.evidence-focus{padding:12px;margin-bottom:12px;border:1px solid var(--c-border);border-radius:8px;color:var(--c-accent);font-size: 15px;line-height:1.7}</style>
