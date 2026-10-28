<script setup>
import { computed, onMounted, onBeforeUnmount, ref, toRaw, useId, watch } from 'vue'
import { api } from '../api/client'
import { useSessionStore } from '../stores/session'
import QualitySummary from './QualitySummary.vue'

const store = useSessionStore()
const result = ref(null), loading = ref(false), error = ref(''), category = ref('review'), offset = ref(0)
const settings = ref(null), saving = ref(false), saved = ref(false)
const settingsBaseline = ref(''), settingsConflict = ref(false)
const reviewExpanded = ref(false), reviewListId = `quality-review-${useId()}`
const limit = 50
const busy = computed(() => loading.value || saving.value || store.loading || store.chatRunning || store.switchingProject)
const settingsDirty = computed(() => !!settings.value && JSON.stringify(settings.value) !== settingsBaseline.value)
let requestVersion = 0, saveVersion = 0, disposed = false
const names = { ammonia_nitrogen: '氨氮', dissolved_oxygen: 'DO' }
function context() {
  const workspace=store.workspaceRevision, project=store.projectState.current?.id, revision=store.status.analysis_revision
  return () => !disposed && !store.switchingProject && workspace===store.workspaceRevision
    && project===store.projectState.current?.id && revision===store.status.analysis_revision
}
function adoptSettings(value) {
  if (!value) return
  const fingerprint=JSON.stringify(value)
  if (!settings.value || !settingsDirty.value) {
    settings.value=structuredClone(value);settingsBaseline.value=fingerprint;settingsConflict.value=false
  } else settingsConflict.value=fingerprint!==settingsBaseline.value
}
async function load() {
  if (disposed || store.switchingProject) return
  const version = ++requestVersion, current=context()
  result.value = null; error.value = ''
  if (!store.status.has_data) { loading.value = false; return }
  loading.value = true
  try {
    let response = await api.dataQuality({ category: category.value, offset: offset.value, limit })
    if (!current() || version!==requestVersion) return
    if (offset.value>0 && offset.value>=response.total) {
      offset.value=0;response=await api.dataQuality({category:category.value,offset:0,limit})
      if (!current() || version!==requestVersion) return
    }
    result.value=response;adoptSettings(response.summary?.['参数'])
  } catch (e) { if (current() && version === requestVersion) error.value = e.message }
  finally { if (version === requestVersion) loading.value = false }
}
function reset() { offset.value = 0; return load() }
function resetData() {
  settings.value=null;settingsBaseline.value='';settingsConflict.value=false;saved.value=false
  return reset()
}
function reloadSettings() { if (!busy.value) return resetData() }
async function saveSettings() {
  if (busy.value || !settings.value || settingsConflict.value) return
  const version=++saveVersion, current=context(), submitted=structuredClone(toRaw(settings.value))
  saving.value = true; saved.value = false; error.value = ''
  try {
    await store.updateQualityConfig(submitted)
    if (!current() || version!==saveVersion) return
    settings.value=structuredClone(submitted);settingsBaseline.value=JSON.stringify(submitted);settingsConflict.value=false
    await load()
    if (current() && version===saveVersion && !error.value) saved.value=true
  } catch (e) { if (current() && version===saveVersion) error.value = e.message }
  finally { if (version===saveVersion) saving.value = false }
}
function page(delta) {
  if (busy.value || !result.value) return
  const next=offset.value+delta*limit
  if (next<0 || next>=result.value.total) return
  offset.value=next;return load()
}
onMounted(load)
watch(() => [store.workspaceRevision,store.projectState.current?.id,store.status.analysis_revision,
  store.switchingProject,store.status.results_revision,store.status.rows], (values,previous) => {
  if (values.slice(0,3).some((value,i)=>value!==previous[i])) {
    requestVersion++;saveVersion++;result.value=null;loading.value=false;saving.value=false
    error.value='';reviewExpanded.value=false
    resetData();return
  }
  if (values[3]!==previous[3]) {
    requestVersion++;saveVersion++;loading.value=false;saving.value=false;saved.value=false
    if (!store.switchingProject) load()
    return
  }
  if (!saving.value) { saved.value=false;load() }
})
onBeforeUnmount(() => { disposed=true;requestVersion++;saveVersion++ })
</script>

<template>
  <div class="quality-panel">
    <p>用于定位需要复核的记录，不自动剔除高值或短时污染信号。</p>
    <QualitySummary :summary="result?.summary" />
    <details v-if="settings" class="settings">
      <summary>调整本项目的质控阈值</summary>
      <p>工程门限需要结合采样间隔、传感器分辨率与校准资料确定；不会改变污染判定阈值。参数随项目保存。</p>
      <form @submit.prevent="saveSettings">
        <fieldset :disabled="busy">
        <label>最大相邻采样间隔（秒）<input v-model.number="settings.max_gap_s" type="number" min="1" max="86400" step="any" required></label>
        <label>恒值段最小时长（秒）<input v-model.number="settings.flat_duration_s" type="number" min="1" max="86400" step="any" required></label>
        <label>恒值段最少独立时刻<input v-model.number="settings.flat_min_points" type="number" min="3" max="1000" step="1" required></label>
        <label>氨氮突刺门限（mg/L）<input v-model.number="settings.spike_thresholds.ammonia_nitrogen" type="number" min="0.000001" max="1000" step="any" required></label>
        <label>DO突刺门限（mg/L）<input v-model.number="settings.spike_thresholds.dissolved_oxygen" type="number" min="0.000001" max="20" step="any" required></label>
        <label>氨氮恒值容差（mg/L）<input v-model.number="settings.flat_tolerances.ammonia_nitrogen" type="number" min="0" max="100" step="any" required></label>
        <label>DO恒值容差（mg/L）<input v-model.number="settings.flat_tolerances.dissolved_oxygen" type="number" min="0" max="20" step="any" required></label>
        <button type="submit" :disabled="busy || settingsConflict">{{ saving ? '应用中…' : '应用并重算质控' }}</button>
        </fieldset>
      </form>
    </details>
    <p v-if="settingsConflict" role="alert">后台质控参数已经变化，当前草稿已保留。请先载入最新参数，再调整并应用。</p>
    <button v-if="settingsDirty || settingsConflict" type="button" :disabled="busy" @click="reloadSettings">放弃草稿并重新载入参数</button>
    <p v-if="saved">已应用新质控参数；原始预警仍保留，历史报告需要重新分析。请保存项目以保留参数。</p>
    <div class="review-heading">
      <button type="button" class="review-toggle" :aria-expanded="reviewExpanded" :aria-controls="reviewListId" @click="reviewExpanded = !reviewExpanded">
        {{ reviewExpanded ? '隐藏复核清单' : '展开复核清单' }}
      </button>
      <span v-if="result" class="review-count">当前类别 {{ result.total }} 条</span>
    </div>
    <p v-if="error && !reviewExpanded" role="alert">{{ error }}</p>
    <div :id="reviewListId" v-show="reviewExpanded">
      <label>复核清单
        <select v-model="category" :disabled="busy" @change="reset">
          <option value="review">需质控复核</option><option value="missing">关键指标缺测</option>
          <option value="invalid">输入无效</option><option value="unevaluated">时序上下文不足</option><option value="all">全部记录</option>
        </select>
      </label>
      <button type="button" :disabled="busy" @click="load">刷新</button>
      <p v-if="loading">正在检查记录…</p>
      <p v-else-if="error" role="alert">{{ error }}</p>
      <template v-else-if="result">
        <p>当前类别共 {{ result.total }} 条。没有复核标记不代表数据已经校准。</p>
        <details v-for="record in result.records" :key="record.id" class="quality-record">
          <summary>采样点 #{{ record.id + 1 }} · {{ record.time }} · {{ record.label }}</summary>
          <p>{{ record.coordinate || '没有有效GPS坐标' }}<span v-if="record.input_record_id"> · 排序前导入记录 #{{ record.input_record_id }}</span></p>
          <ul><li v-for="(reason, i) in record.reasons" :key="i">{{ names[reason.parameter] || '采样' }}：{{ reason.message }}</li></ul>
          <p v-for="(value, key) in record.raw_text" :key="key">{{ names[key] }}导入原值：{{ record.raw_preserved[key] ? value ?? '缺测' : '旧项目未单独保留原值' }}</p>
        </details>
        <div v-if="result.total" class="pagination">
          <button type="button" :disabled="busy || offset === 0" @click="page(-1)">上一页</button>
          <span>{{ offset + 1 }}–{{ Math.min(offset + limit, result.total) }}</span>
          <button type="button" :disabled="busy || offset + limit >= result.total" @click="page(1)">下一页</button>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
p, li, label, summary, .pagination { color: var(--c-sub); font-size: 14px; line-height: 1.7; }
select, button { margin: 4px 8px; padding: 6px 12px; border: 1px solid var(--c-border); border-radius: 6px; background: var(--c-surface-2); color: var(--c-ink); }
button, summary { cursor: pointer; }
button:disabled { opacity: .5; cursor: default; }
.review-heading { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 12px; }
.review-toggle { margin-left: 0; }
.review-count { color: var(--c-sub); font-size: 14px; }
.quality-record { border-bottom: 1px solid var(--c-border); padding: 9px 0; }
.settings { margin-bottom: 12px; }
.settings fieldset { border: 0; padding: 0; margin: 0; min-width: 0; display: grid; grid-template-columns: repeat(auto-fit,minmax(180px,1fr)); gap: 10px; }
.settings label { display: flex; flex-direction: column; }
.settings input { width: 100%; box-sizing: border-box; padding: 7px; color: var(--c-ink); background: var(--c-surface-2); border: 1px solid var(--c-border); border-radius: 6px; }
.pagination { display: flex; align-items: center; justify-content: flex-end; gap: 10px; margin-top: 10px; }
</style>
