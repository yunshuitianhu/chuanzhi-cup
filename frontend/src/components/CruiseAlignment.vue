<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../api/client'
import { useSessionStore } from '../stores/session'
import { numberText } from '../utils/mapInteraction'

const props = defineProps({ revision:{default:null}, modelBusy:Boolean })
const emit = defineEmits(['updated'])
const store = useSessionStore()
const audit=ref(null), result=ref(null), error=ref(''), busy=ref(false)
const timezone=ref('Asia/Shanghai'), origin=ref(''), path=ref(''), note=ref(''), confirmed=ref(false)
const offset=ref(0), distance=ref(50), ambiguity=ref(5), maxSpeed=ref(10), constituent=ref('ammonia')
const start=ref(0), end=ref(1), reverse=ref(false)
const locked=computed(()=>busy.value || props.modelBusy || store.chatRunning || store.loading || !store.ready)
const labels={matched:'可作情景对照',invalid_time:'时间无效',invalid_gps:'GPS无效或非原始',gps_jump:'航速异常线索',
  off_reference:'距参考线过远',ambiguous_position:'位置有歧义',outside_time:'超出模拟时段',outside_chainage:'超出模型桩号',
  unobserved_measurement:'缺少原始有效测值',duplicate_observation:'重复时空观测'}
let generation=0, controller, disposed=false
function cancel() { generation++; controller?.abort() }
function configChanged() {confirmed.value=false;result.value=null}

async function load() {
  cancel(); const token=generation, workspace=store.workspaceRevision
  controller=new AbortController(); error.value='';result.value=null;audit.value=null
  if(!store.status.has_data)return
  busy.value=true
  try {
    const [a,r]=await Promise.all([api.cruiseAudit(timezone.value,controller.signal),api.cruiseAlignment(controller.signal)])
    if(disposed || token!==generation || workspace!==store.workspaceRevision)return
    audit.value=a; end.value=Math.max(1,a.records-1)
    if(r.config) {
      const c=r.config
      path.value=JSON.stringify(c.reference_path);origin.value=c.model_origin;timezone.value=c.monitoring_timezone
      note.value=c.reference_note;offset.value=c.time_offset_s;distance.value=c.max_distance_m
      ambiguity.value=c.ambiguity_m;maxSpeed.value=c.max_speed_mps;constituent.value=c.constituent;confirmed.value=c.confirmed
      result.value=r
    } else {
      path.value='';note.value='';confirmed.value=false;origin.value=a.first_time || ''
    }
  } catch(e) {if(token===generation && e.name!=='AbortError')error.value=e.message}
  finally {if(token===generation)busy.value=false}
}
async function candidate() {
  const token=generation, workspace=store.workspaceRevision
  busy.value=true;error.value=''
  try {
    const value=await api.cruiseReference({start:start.value,end:end.value,reverse:reverse.value},controller?.signal)
    if(disposed || token!==generation || workspace!==store.workspaceRevision)return
    path.value=JSON.stringify(value.reference_path);note.value=value.reference_note;configChanged()
  } catch(e) {if(token===generation && e.name!=='AbortError')error.value=e.message}
  finally {if(token===generation)busy.value=false}
}
async function run() {
  const token=generation, workspace=store.workspaceRevision
  busy.value=true;error.value='';result.value=null
  try {
    const payload={reference_path:JSON.parse(path.value),model_origin:origin.value,monitoring_timezone:timezone.value,
      time_offset_s:offset.value,max_distance_m:distance.value,ambiguity_m:ambiguity.value,
      max_speed_mps:maxSpeed.value,confirmed:confirmed.value,reference_note:note.value,constituent:constituent.value}
    const value=await api.setCruiseAlignment(payload,controller?.signal)
    if(disposed || token!==generation || workspace!==store.workspaceRevision)return
    result.value=value
    await store.refreshStatus()
    emit('updated')
  } catch(e) {if(token===generation && e.name!=='AbortError')error.value=e instanceof SyntaxError?'参考线须为JSON二维数组，例如 [[纬度,经度],[纬度,经度]]':e.message}
  finally {if(token===generation)busy.value=false}
}
onMounted(load)
watch([()=>store.workspaceRevision,()=>store.status.analysis_revision,()=>store.status.has_data,()=>props.revision],load)
onBeforeUnmount(()=>{disposed=true;cancel()})
</script>

<template>
  <section class="alignment">
    <h3>⑤ 走航时空配准与情景对照</h3>
    <p>每条观测按自己的采样时间与空间位置读取模型浓度。返航会回到相同桩号，不把累计航程当作河道长度；不自动猜测水流方向或把整次走航当作同一时刻。</p>
    <button type="button" :disabled="locked" @click="load">重新核对当前轨迹</button>
    <p v-if="!store.status.has_data">请先加载走航数据。逐点对照还需要当前污染情景模拟结果。</p>
    <div v-if="audit" class="audit">
      <span>原始有效GPS {{audit.valid_gps}} / {{audit.records}}</span>
      <span>累计航程 {{numberText(audit.travelled_distance_m,1)}} m</span>
      <span>首末距离 {{numberText(audit.endpoint_distance_m,1)}} m</span>
      <p v-for="warning in audit.warnings" :key="warning" class="warning">{{warning}}</p>
      <p>{{audit.notice}}</p>
    </div>
    <details>
      <summary>配置参考线与模拟时间原点</summary>
      <p>优先提供独立核实的河道中心线。也可从当前数据选取单向完整航段作为候选；所选方向须与模型上游→下游一致。坐标为WGS84的[纬度,经度]，不会按模型长度缩放。</p>
      <div class="fields">
        <label>候选段起始记录（从0起）<input v-model.number="start" type="number" min="0" :disabled="locked" /></label>
        <label>候选段结束记录<input v-model.number="end" type="number" min="1" :disabled="locked" /></label>
      </div>
      <label class="check"><input v-model="reverse" type="checkbox" :disabled="locked" />反转所选候选段</label>
      <button type="button" :disabled="locked || !audit" @click="candidate">填入候选参考线</button>
      <label>参考线JSON<textarea v-model="path" rows="4" placeholder="[[纬度,经度],[纬度,经度]]" :disabled="locked" @input="configChanged" /></label>
      <label>参考线来源与核对说明<input v-model="note" :disabled="locked" @input="configChanged" /></label>
      <div class="fields">
        <label>模拟T=0的绝对时间<input v-model="origin" placeholder="2025-08-01T10:33:00+08:00" :disabled="locked" @input="configChanged" /></label>
        <label>监测记录时区<input v-model="timezone" placeholder="Asia/Shanghai" :disabled="locked" @input="configChanged" /></label>
        <label>仪器时钟修正 秒<input v-model.number="offset" type="number" :disabled="locked" @input="configChanged" /></label>
        <label>离参考线最大距离 m<input v-model.number="distance" type="number" min="0.5" max="500" :disabled="locked" @input="configChanged" /></label>
        <label>位置歧义距离容差 m<input v-model.number="ambiguity" type="number" min="0" max="50" :disabled="locked" @input="configChanged" /></label>
        <label>航速排查阈值 m/s<input v-model.number="maxSpeed" type="number" min="0.1" max="50" :disabled="locked" @input="configChanged" /></label>
        <label>对照指标<select v-model="constituent" :disabled="locked" @change="configChanged"><option value="ammonia">氨氮</option><option value="do">溶解氧</option></select></label>
      </div>
      <p>时钟修正为“记录时间＋修正秒数”；正值把记录向后移。建议时间来自首条可解析记录，仍须核实是否对应模拟T=0。距离与航速阈值是排查设定，不代表仪器已校准。</p>
      <label class="check"><input v-model="confirmed" type="checkbox" :disabled="locked" />我已核对参考线按模型上游至下游排列，并核对模拟时间原点</label>
      <button type="button" :disabled="locked || !confirmed || !path || !origin || !note" @click="run">保存配置并逐点对照</button>
    </details>
    <p v-if="error" role="alert" class="warning">{{error}}</p>
    <div v-if="result" class="comparison">
      <p>可对照 {{result.summary.matched}} / {{result.summary.records}} 条；排除 {{result.summary.excluded}} 条。配准配置随河流项目保存。</p>
      <p v-if="result.summary.comparison">情景对照 RMSE {{numberText(result.summary.comparison.rmse_mgL,4)}} mg/L；平均偏差 {{numberText(result.summary.comparison.bias_mgL,4)}} mg/L。</p>
      <p v-else>没有满足当前时空范围与质量规则的配对观测，不计算误差。</p>
      <p class="warning">{{result.summary.notice}}</p>
      <div class="table-scroll"><table><thead><tr><th>记录</th><th>状态</th><th>相对时间 h</th><th>桩号 m</th><th>观测 mg/L</th><th>模拟 mg/L</th></tr></thead>
        <tbody><tr v-for="row in result.rows.slice(0,20)" :key="row.record_id"><td>{{row.record_id}}</td><td>{{labels[row.status] || row.status}}</td><td>{{numberText(row.time_h,4)}}</td><td>{{numberText(row.chainage_m,1)}}</td><td>{{numberText(row.measured_mgL,3)}}</td><td>{{numberText(row.simulated_mgL,3)}}</td></tr></tbody>
      </table></div>
      <p>显示前20条；统计覆盖全部记录。超出时空范围、离线过远、位置歧义、航速异常、重复或缺测记录不进入误差统计。</p>
    </div>
  </section>
</template>

<style scoped>
.alignment { margin-top:20px; padding:18px; background:var(--c-surface-2); border:1px solid var(--c-border); border-radius:9px; color:var(--c-sub); font-size:12px; line-height:1.8; }
h3 { margin:0 0 10px; color:var(--c-ink); font-size:14px; }
p { margin:8px 0; }
.fields { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px 12px; }
label { display:flex; flex-direction:column; gap:4px; margin:8px 0; }
input,textarea,select { width:100%; box-sizing:border-box; padding:8px; border:1px solid var(--c-border); border-radius:6px; color:var(--c-ink); background:var(--c-surface); font:inherit; }
.check { flex-direction:row; align-items:flex-start; gap:8px; }.check input { width:auto; margin-top:5px; }
button { padding:8px 12px; color:var(--c-ink); border:1px solid var(--c-border); border-radius:6px; background:var(--c-surface); cursor:pointer; }
button:disabled { opacity:.5; cursor:default; }
.warning { color:#dfb85a; }.audit { margin:10px 0; }.audit span { display:inline-block; margin-right:15px; }
summary { cursor:pointer; color:var(--c-ink); margin-top:12px; }.table-scroll { overflow:auto; }table { width:100%; min-width:650px; border-collapse:collapse; }th,td { text-align:left; padding:7px; border-bottom:1px solid var(--c-border); }
@media(max-width:650px) { .fields { grid-template-columns:1fr; } }
</style>
