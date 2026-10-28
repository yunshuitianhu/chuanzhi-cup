<script setup>
/**
 * 数据上传面板 —— 对应 app.py 侧边栏的「数据上传与配置」区。
 *
 * 交互与原版一致：
 *   1. 选文件（走航数据必选，视频可选）
 *   2. 点「加载数据」才真正上传并解析
 * 之所以不在选择文件后立即上传：大视频上传耗时，原版特意让用户
 * 「等文件名出现后再点加载」，避免误以为卡住。这里保留该节奏。
 */
import { ref, computed, onMounted, watch } from 'vue'
import { useSessionStore } from '../stores/session'
import { toastOk, toastErr } from '../composables/useToast'

const store = useSessionStore()

const dataFile = ref(null)
const videoFile = ref(null)
const dataInput = ref(null)
const videoInput = ref(null)
const report = ref('')
const sampleBusy = ref('')
const sampleError = ref('')
const sampleReport = ref(store.sampleLastReport || '')
const sampleDetectionError = ref('')
const samples = computed(() => store.sampleCatalog || [])
const sampleDisabled = computed(() => !!sampleBusy.value || store.loading || store.chatRunning || store.switchingProject)

const canLoad = computed(() => (!!dataFile.value || (!!videoFile.value && store.status.has_data)) && !store.loading && !store.chatRunning)

/**
 * 文件选择框的显示文本。
 *
 * 浏览器出于安全**不允许回填 `<input type=file>`**，所以刷新页面后选择框
 * 必然是空的。但后端会话里的数据还在。若只依据本地选择框显示，就会出现
 * 「选择走航数据」与状态区的「1805 条」并存 —— 自相矛盾，用户会以为
 * 没上传数据却跑出了检测结果（已由用户实际反馈）。
 *
 * 因此：本地区选了文件 → 显示文件名；否则若会话里已有数据 → 显示「已加载」。
 */
const dataLabel = computed(() => {
  if (dataFile.value) return dataFile.value.name
  const s = store.status
  if (s.has_data) return `已加载：${s.data_filename || `${s.rows} 条记录`}`
  return '选择走航数据 (xlsx/xls/csv)'
})

const videoLabel = computed(() => {
  if (videoFile.value) return videoFile.value.name
  const s = store.status
  if (s.video_frames) return `已加载：${s.video_filename || `${s.video_frames} 帧`}`
  return '选择样本视频 (可选)'
})

/** 会话里已加载过数据 → 按钮改为「重新选择并加载」，语义更准确。 */
const loadLabel = computed(() => {
  if (store.loading) return '处理中…'
  if (!dataFile.value && videoFile.value && store.status.has_data) return '加载视频'
  return store.status.has_data ? '重新加载数据' : '加载数据'
})

function onPick(kind, e) {
  const f = e.target.files?.[0] || null
  if (kind === 'data') dataFile.value = f
  else videoFile.value = f
}

function clearPick(kind) {
  if (kind === 'data') { dataFile.value = null; if (dataInput.value) dataInput.value.value = '' }
  else { videoFile.value = null; if (videoInput.value) videoInput.value.value = '' }
}

function detectionNotice(detection) {
  if (!detection?.requested) return ''
  if (detection.ok) return detection.saved === false
    ? (detection.warning || '筛查结果仅在当前会话可用，项目保存失败。')
    : ''
  return `数据已载入，但筛查未完成：${detection.error || '未知原因'}`
}
function syncSampleNotice() {
  sampleReport.value = store.sampleLastReport || ''
  sampleDetectionError.value = detectionNotice(store.sampleLastDetection)
}
watch(() => store.sampleLastReport, value => { sampleReport.value = value || '' })
watch(() => store.sampleLastDetection, value => { sampleDetectionError.value = detectionNotice(value) })
onMounted(async () => {
  syncSampleNotice()
  try { await store.loadSamples() }
  catch (e) { sampleError.value = e.message }
})
async function reloadSamples() {
  sampleError.value = ''
  try { await store.loadSamples(true) }
  catch (e) { sampleError.value = e.message }
}
async function loadSample(sample, detect) {
  if (sampleDisabled.value) return
  if (dataFile.value || videoFile.value) {
    sampleError.value = '已有待上传文件；请先点击“加载数据”或清除选择，再载入示例。'
    return
  }
  sampleBusy.value = sample.id + (detect ? '-detect' : '-load')
  sampleError.value = ''; sampleReport.value = ''; sampleDetectionError.value = ''
  try {
    const res = await store.loadSample(sample.id, detect)
    sampleReport.value = store.sampleLastReport
    sampleDetectionError.value = detectionNotice(res.detection)
    if (sampleDetectionError.value) toastOk('示例数据已载入；筛查未完成，请查看提示。')
    else if (res.detection?.ok) toastOk(`示例已载入并完成基础筛查：${res.detection.black_spots ?? 0} 个预警点`)
    else toastOk(`示例已载入：${res.rows} 条记录`)
  } catch (e) {
    sampleError.value = e.message
    toastErr(`示例载入失败：${e.message}`)
  } finally { sampleBusy.value = '' }
}
async function load() {
  if (!canLoad.value) return
  try {
    if (dataFile.value) {
      const res = await store.uploadData(dataFile.value)
      report.value = res.report
      clearPick('data')
      toastOk(`数据加载完成：${res.rows} 条记录`)
    }
    if (videoFile.value) {
      const v = await store.uploadVideo(videoFile.value)
      report.value = v.report
      clearPick('video')
      toastOk(`视频抽帧完成：${v.frames} 帧`)
    }
  } catch (e) {
    toastErr(`加载失败：${e.message}`)
  }
}
</script>

<template>
  <section class="panel">
    <h3 class="panel-title">数据与示例</h3>

    <section id="data-samples" class="sample-section" :class="{prominent:!store.status.has_data}" aria-label="内置仿真示例">
      <div class="sample-head">
        <div><strong>试用仿真示例</strong><span class="sample-tag">仅监测表 · 仿真</span></div>
        <button v-if="store.sampleCatalogError || sampleError" type="button" class="sample-retry" :disabled="store.loading" @click="reloadSamples">重新读取目录</button>
      </div>
      <p class="hint sample-intro">没有数据也可以先体验图表、质控和阈值筛查；载入会先保存当前项目，在独立演示项目中打开。基础示例和筛查不需要模型 Key；AI 解读与报告需另行配置。</p>
      <p v-if="sampleError" class="sample-error" role="alert">{{sampleError}}</p>
      <p v-else-if="store.sampleCatalogError" class="sample-error" role="alert">{{store.sampleCatalogError}}</p>
      <p v-if="sampleBusy" class="hint" role="status">正在载入示例并准备数据，请稍候…</p>
      <p v-if="sampleReport" class="sample-report" role="status">{{sampleReport}}</p>
      <p v-if="sampleDetectionError" class="sample-error" role="alert">{{sampleDetectionError}}</p>
      <ul v-if="samples.length" class="sample-list">
        <li v-for="sample in samples" :key="sample.id" class="sample-item">
          <div class="sample-copy">
            <strong>{{sample.title}}</strong>
            <span class="sample-meta">{{sample.data_kind === 'synthetic' ? '仿真' : sample.data_kind}} · {{sample.rows}} 条 · 仅监测表</span>
            <p>{{sample.description}}</p>
          </div>
          <div class="sample-actions">
            <button type="button" class="sample-load" :disabled="sampleDisabled" @click="loadSample(sample,false)">{{sampleBusy === sample.id+'-load' ? '载入中…' : '载入'}}</button>
            <button type="button" class="sample-detect" :disabled="sampleDisabled" @click="loadSample(sample,true)">{{sampleBusy === sample.id+'-detect' ? '筛查中…' : '载入并检测'}}</button>
          </div>
        </li>
      </ul>
      <p v-else-if="!store.sampleCatalogLoaded && !sampleError && !store.sampleCatalogError" class="hint">正在读取示例目录…</p>
      <p v-if="store.chatRunning" class="hint">AI 任务进行中，完成后再切换示例。</p>
    </section>

    <h3 class="panel-title upload-title">上传自己的数据</h3>

    <label class="file-btn" :class="{ filled: dataFile || store.status.has_data }">
      <input
        ref="dataInput" type="file" accept=".xlsx,.xls,.csv" hidden
        @change="onPick('data', $event)"
      />
      <span class="ico" aria-hidden="true">{{ store.status.has_data && !dataFile ? '✓' : '表' }}</span>
      <span class="file-copy"><span class="file-kind">监测数据</span><span class="txt" :title="dataLabel">{{ dataLabel }}</span></span>
      <button v-if="dataFile" class="clear" @click.prevent="clearPick('data')" title="移除">×</button>
    </label>

    <label class="file-btn" :class="{ filled: videoFile || store.status.video_frames }">
      <input
        ref="videoInput" type="file" accept=".mp4,.avi,.mov" hidden
        @change="onPick('video', $event)"
      />
      <span class="ico" aria-hidden="true">{{ store.status.video_frames && !videoFile ? '✓' : '影' }}</span>
      <span class="file-copy"><span class="file-kind">巡航视频 · 可选</span><span class="txt" :title="videoLabel">{{ videoLabel }}</span></span>
      <button v-if="videoFile" class="clear" @click.prevent="clearPick('video')" title="移除">×</button>
    </label>

    <button class="load-btn" :disabled="!canLoad" @click="load">{{ loadLabel }}</button>
    <p v-if="store.chatRunning" class="hint">AI 分析进行中，完成后可重新加载数据。</p>

    <!-- 已有数据但未选新文件时，说明清楚点按钮不会重置 -->
    <p v-if="store.status.has_data && !dataFile" class="hint loaded-note">
      数据已加载。可单独上传或替换视频；选择新监测表重新加载会清空分析和模型。
    </p>
    <p v-if="videoFile && !dataFile && store.status.has_data" class="hint">加载新视频会清除旧交叉印证和报告完成标记，保留监测表、检测结果及河道模型。</p>

    <p v-if="videoFile" class="hint">
      ⚠ 大视频上传需要时间，请等文件名出现后再点「加载数据」。
    </p>

    <div v-if="store.status.has_data" class="stat">
      <p v-if="store.status.data_identity" class="hint" :title="store.status.data_identity['说明']">
        {{ store.status.data_identity.label }}
      </p>
      <div class="stat-row"><span>走航数据</span><b>{{ store.status.rows }} 条</b></div>
      <div class="stat-row"><span>视频帧</span><b>{{ store.status.video_frames }} 帧</b></div>
      <div v-if="store.status.sample_meta" class="stat-row"><span>示例</span><b class="small">{{store.status.sample_meta.id}} · {{store.status.sample_meta.version}}</b></div>
      <div class="stat-row" v-if="store.status.time_range">
        <span>时段</span>
        <b class="small">{{ store.status.time_range[0].slice(5, 16) }} ~ {{ store.status.time_range[1].slice(11, 16) }}</b>
      </div>
    </div>

    <details v-if="report" class="report">
      <summary>加载详情</summary>
      <pre>{{ report }}</pre>
    </details>
  </section>
</template>

<style scoped>
.panel { padding:0 0 20px; border-bottom:1px solid var(--c-border); min-width:0; }
.panel-title { margin:0 0 12px; font-size:13px; font-weight:500; color:var(--c-sub); letter-spacing:.08em; }
.upload-title { margin-top:18px; }
.sample-section { border:1px solid var(--c-border); border-radius:var(--radius-sm); padding:11px 10px; margin-bottom:14px; background:var(--c-bg); min-width:0; }
.sample-section.prominent { border-color:var(--c-accent); background:var(--c-surface-2); }
.sample-head { display:flex; align-items:flex-start; justify-content:space-between; gap:8px; font-size:12px; color:var(--c-ink); }
.sample-head > div { display:flex; flex-wrap:wrap; align-items:baseline; gap:6px; min-width:0; }
.sample-tag { color:var(--c-accent); border:1px solid var(--c-border); border-radius:9px; padding:1px 6px; font-size:12px; }
.sample-retry { border:1px solid var(--c-border); background:var(--c-surface); color:var(--c-sub); border-radius:5px; font:inherit; font-size:12px; padding:3px 7px; min-height:26px; }
.sample-intro { margin:6px 0 8px; }
.sample-list { list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:7px; }
.sample-item { border-top:1px solid var(--c-border-soft); padding-top:7px; min-width:0; }
.sample-copy strong { display:block; font-size:13px; color:var(--c-ink); line-height:1.5; }
.sample-meta { display:block; font-size:12px; color:var(--c-accent); margin-top:1px; }
.sample-copy p { margin:3px 0 0; font-size:12px; color:var(--c-sub); line-height:1.65; }
.sample-actions { display:flex; gap:6px; margin-top:6px; }
.sample-actions button { min-height:30px; border-radius:5px; font:inherit; font-size:12px; padding:4px 9px; }
.sample-load { background:var(--c-accent); border:1px solid var(--c-accent); color:#06121c; font-weight:600; }
.sample-detect { background:var(--c-surface); border:1px solid var(--c-border); color:var(--c-ink); }
.sample-actions button:disabled { opacity:.45; cursor:not-allowed; }
.sample-error { margin:5px 0; color:#ffb7a7; font-size:12px; line-height:1.6; }
.sample-report { margin:7px 0 0; color:var(--c-sub); font-size:12px; line-height:1.6; }
@media(max-width:600px) { .sample-actions button { flex:1; } }
.file-btn { display:flex; align-items:center; gap:10px; min-width:0; border:1px dashed var(--c-border); border-radius:var(--radius-sm); padding:11px 10px; margin-bottom:9px; cursor:pointer; font-size:13px; color:var(--c-sub); background:var(--c-bg); transition:border-color var(--duration-fast),background var(--duration-fast); }
.file-btn:hover { border-color:var(--c-accent); color:var(--c-ink); }
.file-btn.filled { border-style:solid; color:var(--c-ink); }
.ico { display:grid; place-items:center; width:30px; height:34px; flex-shrink:0; border:1px solid var(--c-border); border-radius:5px; color:var(--c-sub); font-size:13px; }
.filled .ico { color:var(--c-accent); }
.file-copy { display:flex; flex-direction:column; gap:3px; min-width:0; flex:1; }
.file-kind { color:var(--c-sub); font-size:12px; letter-spacing:.03em; }
.txt { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; line-height:1.6; }
.clear { background:transparent; border:none; color:var(--c-sub); font-size:18px; line-height:1; padding:4px; }
.clear:hover { color:#ff6b81; }
.load-btn { width:100%; min-height:39px; padding:9px; border-radius:var(--radius-sm); border:none; background:var(--c-accent); color:#06121c; font-weight:600; transition:opacity var(--duration-fast); }
.load-btn:disabled { opacity:.4; cursor:not-allowed; }
.load-btn:not(:disabled):hover { opacity:.88; }
.hint { margin:10px 0 0; font-size:12px; color:var(--c-sub); line-height:1.8; }
.loaded-note { color:var(--c-sub); }
.stat { margin-top:16px; padding-top:12px; border-top:1px solid var(--c-border); }
.stat > .hint { display:inline-block; margin:0 0 8px; border:1px solid var(--c-border); border-radius:4px; padding:2px 6px; line-height:1.5; }
.stat-row { display:flex; justify-content:space-between; align-items:baseline; font-size:13px; padding:4px 0; gap:8px; color:var(--c-sub); }
.stat-row b { color:var(--c-ink); font-weight:600; font-variant-numeric:tabular-nums; text-align:right; }
.stat-row b.small { font-size:12px; font-weight:400; }
.report { margin-top:8px; font-size:12px; }
.report summary { cursor:pointer; color:var(--c-sub); }
.report pre { margin:6px 0 0; padding:10px; background:var(--c-bg); border-radius:var(--radius-sm); white-space:pre-wrap; word-break:break-all; color:var(--c-sub); font-size:12px; }
</style>
