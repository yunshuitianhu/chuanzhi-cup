<script setup>
import {computed,onBeforeUnmount,onMounted,ref,watch} from 'vue'
import {api} from '../api/client'
import {useSessionStore} from '../stores/session'
const store=useSessionStore(),emit=defineEmits(['updated'])
const report=ref(null),settings=ref(null),frame=ref(null),selected=ref(null),offset=ref(0)
const roi=ref([0,0,1,1]),scope=ref('all'),decision=ref('pending'),notes=ref('')
const loading=ref(false),frameLoading=ref(false),saving=ref(false),dirty=ref(false),error=ref(''),message=ref('')
let version=0,frameVersion=0,saveVersion=0,disposed=false,anchor=null
const locked=computed(()=>loading.value||frameLoading.value||saving.value||store.loading||store.chatRunning||store.switchingProject)
const overlay=computed(()=>({left:`${roi.value[0]*100}%`,top:`${roi.value[1]*100}%`,
  width:`${(roi.value[2]-roi.value[0])*100}%`,height:`${(roi.value[3]-roi.value[1])*100}%`}))
const names={usable:'可作视觉线索',needs_review:'待复核',invalid:'无法读取'}
function context(){
  const workspace=store.workspaceRevision,project=store.projectState.current?.id,revision=store.status.analysis_revision
  return ()=>!disposed&&!store.switchingProject&&workspace===store.workspaceRevision
    &&project===store.projectState.current?.id&&revision===store.status.analysis_revision
}
function changed(){dirty.value=true;message.value=''}
function changeROI(){changed();if(settings.value)settings.value.roi_confirmed=false}
function changeSync(){changed();if(settings.value)settings.value.sync_confirmed=false}
function changeScope(value){
  if(locked.value||dirty.value||!settings.value||!['all','frame'].includes(value))return
  scope.value=value
  roi.value=[...(value==='all'?settings.value.roi:frame.value?.frame.roi||settings.value.roi)]
}
function point(event){const box=event.currentTarget.getBoundingClientRect();return [
  Math.max(0,Math.min(1,(event.clientX-box.left)/box.width)),Math.max(0,Math.min(1,(event.clientY-box.top)/box.height))]}
function begin(event){if(locked.value||!frame.value)return;anchor=point(event);event.currentTarget.setPointerCapture(event.pointerId)}
function move(event){if(!anchor||locked.value)return;const end=point(event);roi.value=[Math.min(anchor[0],end[0]),Math.min(anchor[1],end[1]),Math.max(anchor[0],end[0]),Math.max(anchor[1],end[1])];changeROI()}
function end(){anchor=null}
async function selectFrame(id){
  if(disposed||store.switchingProject)return
  const ticket=++frameVersion,current=context(),revision=report.value?.revision
  selected.value=id;frame.value=null;frameLoading.value=true;error.value=''
  try{
    const result=await api.videoFrame(id,revision)
    if(!current()||ticket!==frameVersion||revision!==report.value?.revision)return
    frame.value=result;roi.value=[...(scope.value==='all'?settings.value.roi:result.frame.roi)]
    decision.value=result.frame.review.decision;notes.value=result.frame.review.notes
  }catch(e){if(current()&&ticket===frameVersion)error.value=e.message}
  finally{if(ticket===frameVersion)frameLoading.value=false}
}
async function load(){
  if(disposed||store.switchingProject)return
  const ticket=++version,current=context()
  loading.value=true;frame.value=null;error.value='';message.value=''
  try{
    let result=await api.videoQuality(offset.value)
    if(!current()||ticket!==version)return
    if(offset.value>0&&offset.value>=result.total){
      offset.value=0;result=await api.videoQuality(0)
      if(!current()||ticket!==version)return
    }
    report.value=result;settings.value=structuredClone(result.settings);dirty.value=false
    emit('updated',result.settings)
    const id=result.rows.some(r=>r.id===selected.value)?selected.value:result.rows[0]?.id
    if(id!=null)await selectFrame(id)
    else{selected.value=null;frame.value=null}
  }catch(e){if(current()&&ticket===version)error.value=e.message}
  finally{if(ticket===version)loading.value=false}
}
async function save(){
  if(locked.value||!report.value||!settings.value||!frame.value)return
  const ticket=++saveVersion,current=context()
  saving.value=true;error.value=''
  try{
    // 与项目切换、数据上传及Agent共用操作状态，避免跨项目保存。
    await store._run(async()=>{
      if(scope.value==='all')await api.saveVideoSettings({expected_revision:report.value.revision,
        settings:{...settings.value,roi:[...roi.value]},frame_id:selected.value,
        review:{decision:decision.value,reviewer:settings.value.reviewer,notes:notes.value,
          roi:frame.value.frame.review.roi||null}})
      else await api.saveVideoFrame(selected.value,{expected_revision:report.value.revision,
        review:{decision:decision.value,reviewer:settings.value.reviewer,notes:notes.value,roi:[...roi.value]}})
      if(!current()||ticket!==saveVersion)return
      dirty.value=false;await store.refreshStatus()
    })
    if(!current()||ticket!==saveVersion)return
    await load()
    if(current()&&ticket===saveVersion)message.value='已更新当前会话；请保存项目以便重启恢复。原始视频帧保留，旧交叉结果已撤销。'
  }catch(e){if(current()&&ticket===saveVersion)error.value=e.message}
  finally{if(ticket===saveVersion)saving.value=false}
}
function page(delta){if(locked.value||dirty.value)return;offset.value=Math.max(0,offset.value+delta);load()}
function reset(){
  version++;frameVersion++;saveVersion++;anchor=null
  report.value=settings.value=frame.value=selected.value=null
  loading.value=frameLoading.value=saving.value=dirty.value=false
  scope.value='all';roi.value=[0,0,1,1];offset.value=0;error.value=message.value=''
}
watch(()=>[store.workspaceRevision,store.projectState.current?.id,store.status.analysis_revision,store.switchingProject,
  store.status.video_frames,store.status.video_filename,store.status.results_revision],(values,previous)=>{
  if(values.slice(0,3).some((v,i)=>v!==previous[i])){reset();if(!store.switchingProject)load();return}
  if(values[3]!==previous[3]){
    version++;frameVersion++;saveVersion++;anchor=null
    loading.value=frameLoading.value=saving.value=false
    if(!store.switchingProject){
      if(dirty.value)message.value='项目未切换，当前视频复核草稿已保留；可继续核对后保存。'
      else load()
    }
    return
  }
  if(saving.value||store.switchingProject)return
  if(dirty.value){message.value='视频或分析条件已变化，草稿仍保留；保存时会核对版本，也可重新载入。';return}
  load()
})
onMounted(load)
onBeforeUnmount(()=>{disposed=true;version++;frameVersion++;saveVersion++})
</script>

<template>
  <section class="video-quality">
    <div class="heading"><h3>视频画面质量与水面区域</h3><button :disabled="locked" @click="load">{{dirty?'放弃草稿并重新载入':'重新载入视频复核'}}</button></div>
    <p>先查看画面并框选水面，排除岸边、天空和船体。低纹理、反光或曝光异常只提示复核，不直接判断水体浑浊或黑臭。</p>
    <p v-if="error" class="warning" role="alert">{{error}}</p><p v-if="message" role="status">{{message}}</p>
    <p v-if="loading" role="status">正在核对抽帧和画面质量…</p>
    <template v-if="report && settings">
      <div class="stats"><span>抽帧 <b>{{report.summary.frames}}</b></span><span>可作线索 <b>{{report.summary.eligible}}</b></span><span>待复核 <b>{{report.summary.needs_review}}</b></span><span>无效 <b>{{report.summary.invalid}}</b></span><span>人工排除 <b>{{report.summary.manual_excluded}}</b></span></div>
      <div class="frame-list" aria-label="视频抽帧列表"><button v-for="row in report.rows" :key="row.id" :disabled="locked||dirty" :class="{selected:row.id===selected}" @click="selectFrame(row.id)">#{{row.id+1}} · {{row.time_sec??'时间无效'}}s · {{names[row.quality_status]}}</button></div>
      <div class="paging"><button :disabled="locked||dirty||offset===0" @click="page(-20)">上一页帧</button><span>{{offset+1}}—{{Math.min(offset+20,report.total)}} / {{report.total}}</span><button :disabled="locked||dirty||offset+20>=report.total" @click="page(20)">下一页帧</button></div>
      <p v-if="dirty">草稿待保存；保存或放弃草稿后可切换帧及区域应用范围。</p>
      <div v-if="frame" class="frame-evidence">
        <div class="image-area" @pointerdown.prevent="begin" @pointermove="move" @pointerup="end" @pointercancel="end" @lostpointercapture="end" aria-label="拖动框选水面区域">
          <img :src="`data:image/jpeg;base64,${frame.image_base64}`" :alt="`抽帧 ${selected+1}，视频第 ${frame.frame.time_sec} 秒`" draggable="false" />
          <div class="roi" :style="overlay"><span>水面区域</span></div>
        </div>
        <p>当前帧 #{{selected+1}} · T+{{frame.frame.time_sec}}s · {{names[frame.frame.quality_status]}}。{{frame.frame.quality_messages.join('；')}}</p>
        <p v-if="frame.frame.features">清晰度/纹理指标 {{frame.frame.features.laplacian_var.toFixed(1)}} · 亮斑比例 {{(frame.frame.features.glare_fraction*100).toFixed(1)}}% · 过暗比例 {{(frame.frame.features.dark_fraction*100).toFixed(1)}}%</p>
      </div>
      <p v-if="scope==='all' && frame?.frame.review.roi">当前帧已有独立区域，分析时优先使用独立区域；选择“仅当前帧”可查看和编辑。此处正在编辑视频共用区域。</p>
      <fieldset :disabled="locked">
        <div class="fields"><label>区域应用范围<select :value="scope" :disabled="dirty" @change="changeScope($event.target.value)"><option value="all">整个视频（固定机位）</option><option value="frame">仅当前帧（镜头变化）</option></select></label><label>复核人<input v-model="settings.reviewer" maxlength="100" @input="changed" /></label></div>
        <div class="roi-fields"><label v-for="(label,i) in ['左 x','上 y','右 x','下 y']" :key="label">{{label}}（0～1）<input v-model.number="roi[i]" type="number" min="0" max="1" step="0.01" @input="changeROI" /></label></div>
        <template v-if="scope==='all'">
          <label>区域核对说明<input v-model="settings.roi_note" maxlength="1000" placeholder="说明水面范围、机位是否固定；镜头变化时使用逐帧区域" @input="changed" /></label>
          <label class="check"><input v-model="settings.roi_confirmed" type="checkbox" @change="changed" />已核对该区域可用于当前视频的水面线索；不代表水质真值。</label>
          <details><summary>核对视频与传感器同步</summary>
            <div class="fields"><label>视频第0秒相对首条记录偏移（秒）<input v-model.number="settings.time_offset_sec" type="number" step="any" @input="changeSync" /></label><label>匹配窗口宽度（秒）<input v-model.number="settings.time_window_sec" type="number" min="0.001" max="3600" step="any" @input="changeSync" /></label></div>
            <label>同步依据<input v-model="settings.sync_note" maxlength="1000" placeholder="例如共同时间戳、开拍记录；没有依据时保留未确认" @input="changeSync" /></label>
            <label class="check"><input v-model="settings.sync_confirmed" type="checkbox" @change="changed" />已人工核对同步依据与上述偏移/窗口。</label>
          </details>
        </template>
        <div class="fields"><label>当前帧人工处理<select v-model="decision" @change="changed"><option value="pending">待复核</option><option value="include">保留为候选线索</option><option value="exclude">排除本帧</option></select></label><label>当前帧复核说明<input v-model="notes" maxlength="1000" @input="changed" /></label></div>
        <button class="primary" :disabled="!dirty||!frame" @click="save">{{saving?'保存中…':scope==='all'?'保存视频设置与当前帧复核':'保存当前帧区域及复核'}}</button>
      </fieldset>
      <p class="notice">{{report.notice}}人工“保留”不会覆盖画面质量门禁。设置与复核随项目保存；摄像机移动后需重新核对区域。</p>
    </template>
  </section>
</template>

<style scoped>
.video-quality{padding:16px;border:1px solid var(--c-border);border-radius:9px;background:var(--c-surface-2);margin-bottom:18px;font-size:12px;line-height:1.8;color:var(--c-sub)}h3{margin:0;color:var(--c-ink);font-size:15px}.heading,.stats,.paging{display:flex;flex-wrap:wrap;align-items:center;gap:12px}.heading{justify-content:space-between}.stats b{color:var(--c-accent)}.frame-list{display:flex;flex-wrap:wrap;gap:6px;margin:12px 0;max-height:150px;overflow:auto}.selected{outline:2px solid var(--c-accent)}button,input,select{font:inherit;border:1px solid var(--c-border);border-radius:5px;background:var(--c-surface);color:var(--c-ink);padding:7px;box-sizing:border-box;max-width:100%;min-width:0}button{cursor:pointer}button:disabled{opacity:.45;cursor:default}fieldset{min-width:0;border:0;padding:0}label{display:flex;flex-direction:column;gap:4px;margin:8px 0}.fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.roi-fields{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.check{flex-direction:row;align-items:flex-start}.check input{margin-top:5px}.image-area{position:relative;touch-action:none;cursor:crosshair;max-width:760px;margin:14px auto;background:#101c25}.image-area img{display:block;width:100%;height:auto;pointer-events:none;user-select:none}.roi{position:absolute;border:2px solid #4bf4cf;background:#4bf4cf16;box-sizing:border-box;pointer-events:none}.roi span{background:#0b302e;color:#bafbea;padding:2px 5px}.primary{background:var(--c-accent);color:#06121c;margin:10px 0}.warning{color:#ffd08c}.notice{border-top:1px solid var(--c-border);padding-top:10px}summary{cursor:pointer;color:var(--c-ink)}@media(max-width:600px){.fields{grid-template-columns:1fr}.roi-fields{grid-template-columns:repeat(2,minmax(0,1fr))}.video-quality{padding:10px}}
</style>
