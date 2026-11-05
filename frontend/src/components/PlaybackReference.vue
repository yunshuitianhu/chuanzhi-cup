<script setup>
import { ref, watch, onBeforeUnmount } from 'vue'
import { api } from '../api/client'
import { useSessionStore } from '../stores/session'
import { numberText } from '../utils/mapInteraction'
const emit=defineEmits(['updated'])
const store=useSessionStore(), candidate=ref(null), error=ref(''), busy=ref(false)
const start=ref(0),end=ref(1),reverse=ref(false),confirmed=ref(false)
let generation=0,controller=null
function reset(){generation++;controller?.abort();candidate.value=null;confirmed.value=false;busy.value=false;error.value=''}
watch(()=>store.workspaceRevision,reset)
watch(()=>store.status.analysis_revision,reset)
watch([start,end,reverse],()=>{confirmed.value=false})
onBeforeUnmount(reset)
async function run(apply=false){
  const token=++generation,workspace=store.workspaceRevision,revision=store.status.analysis_revision
  controller?.abort();controller=new AbortController();busy.value=true;error.value=''
  try{
    const value=apply ? await api.setPlaybackReference({start:start.value,end:end.value,reverse:reverse.value,
      confirmed:confirmed.value,analysis_revision:revision,project_id:store.projectState.current?.id || null},controller.signal)
      : await api.playbackReference(controller.signal)
    if(token!==generation || workspace!==store.workspaceRevision)return
    if(apply)emit('updated')
    else{candidate.value=value;start.value=value.start;end.value=value.end;reverse.value=false;confirmed.value=false}
  }catch(e){if(token===generation && e.name!=='AbortError')error.value=e.message}
  finally{if(token===generation)busy.value=false}
}
</script>
<template>
  <section class="reference">
    <strong>为当前数据选择地图航段</strong>
    <p>往返巡航可选取其中一段作为地图参考线。先生成建议，再确认本次假定情景的起点和方向。</p>
    <button type="button" :disabled="busy || store.chatRunning || !store.status.has_data" @click="run()">{{busy?'处理中…':'从当前轨迹查找单向航段'}}</button>
    <template v-if="candidate">
      <p>建议航段：记录 {{candidate.start}}–{{candidate.end}} · 约 {{numberText(candidate.length_m,0)}} m。可调整记录范围。</p>
      <div class="fields"><label>起始记录<input v-model.number="start" type="number" min="0" :disabled="busy" /></label><label>结束记录<input v-model.number="end" type="number" min="1" :disabled="busy" /></label></div>
      <label><input v-model="reverse" type="checkbox" :disabled="busy" />反向播放该航段</label>
      <label><input v-model="confirmed" type="checkbox" :disabled="busy" />确认按所选方向作为本次情景的上游→下游；实际水流方向仍需核实</label>
      <button type="button" :disabled="busy || !confirmed || store.chatRunning" @click="run(true)">应用航段并显示地图扩散</button>
      <p>航段配置随项目保存；不会把往返航程累计为河长。已有模拟的长度和浓度保持不变。</p>
    </template>
    <p v-if="error" role="alert">{{error}}</p>
  </section>
</template>
<style scoped>
.reference{margin:14px 0;padding:16px;border:1px solid #365462;border-radius:8px;background:#12232d;color:var(--c-sub);font-size:14px;line-height:1.7;}.reference strong{color:var(--c-text);}p{margin:8px 0;}label{display:flex;gap:8px;align-items:center;margin:8px 0;}button,input{font:inherit;color:var(--c-text);background:#182e3c;border:1px solid #456575;border-radius:5px;padding:7px;}button{cursor:pointer;}button:disabled{opacity:.45;cursor:default;}.fields{display:flex;flex-wrap:wrap;gap:12px;}.fields input{width:85px;}
</style>
