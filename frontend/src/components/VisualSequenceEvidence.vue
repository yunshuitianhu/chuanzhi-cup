<script setup>
/**
 * Q18 阶段 B：用户选定短片段（2—8 帧），先预览再显式发送。
 *
 * 不在选择、翻页或挂载时自动发送；结果不写入报告/排查板。
 * 帧按时间顺序发送，只比较已发送的帧，不把帧间未展示过程写成事实。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../api/client'
import { useSessionStore } from '../stores/session'

const props = defineProps({
  total: { type: Number, default: 0 },
  revision: { type: String, default: '' },
  current: { type: Number, default: null },
  disabled: Boolean,
  collapsed: Boolean,
})
const store = useSessionStore()

const start = ref(1)
const count = ref(4)
const question = ref('按顺序比较这些帧：每帧直接可见什么？帧间有哪些直接可见的变化？看不清、被遮挡或画面相同请说明。')
const preview = ref([])
const result = ref(null)
const busy = ref('')
const error = ref('')
const notice = ref('')
const MAX_FRAMES = 8
const maxCount = computed(() => Math.min(MAX_FRAMES, Math.max(0, props.total)))
const frameNumbers = computed(() => Array.from({ length: count.value }, (_, index) => start.value + index))
const frameIds = computed(() => frameNumbers.value.map(number => number - 1))
const signature = computed(() => JSON.stringify({
  start: start.value,
  count: count.value,
  revision: props.revision,
  workspace: store.workspaceRevision,
  project: store.projectState.current?.id ?? null,
  analysis: store.status.analysis_revision,
  results: store.status.results_revision,
}))
const canPreview = computed(() => !props.disabled && !busy.value && store.ready && !store.loading
  && !store.switchingProject && props.total >= 2 && props.revision.length === 64
  && Number.isInteger(start.value) && start.value >= 1
  && start.value + count.value - 1 <= props.total
  && count.value >= 2 && count.value <= maxCount.value)
const badQuality = (frame) => frame?.quality_status === 'invalid'
  || (frame?.quality_flags || []).some(flag => ['invalid_frame', 'manual_exclusion'].includes(flag))
const sendable = computed(() => preview.value.length === count.value
  && preview.value.every(item => !badQuality(item.frame)))
const canSend = computed(() => sendable.value && !props.disabled && !busy.value
  && store.ready && !store.loading && !store.switchingProject)

let request = 0
let controller = null
let disposed = false

watch(signature, () => {
  request++
  controller?.abort()
  controller = null
  busy.value = ''
  preview.value = []
  result.value = null
  error.value = ''
  notice.value = ''
})
watch(() => props.current, value => {
  if (Number.isInteger(value) && !busy.value) start.value = value + 1
}, { immediate: true })
watch(() => props.total, value => {
  const max = Math.min(MAX_FRAMES, Math.max(0, value))
  if (count.value > max) count.value = Math.max(2, max)
}, { immediate: true })
onMounted(() => {
  if (Number.isInteger(props.current)) start.value = props.current + 1
})

function qualityText(frame) {
  const labels = { usable: '可作线索', needs_review: '待复核', invalid: '无法读取' }
  return labels[frame?.quality_status] || frame?.quality_status || '未判定'
}
const MONITORING_STATUS = {
  matched: '时间窗内有匹配记录',
  partial_match: '部分帧有记录',
  no_match: '时间窗内无记录',
  sync_unconfirmed: '同步未确认',
  no_monitoring: '无监测表',
}
function monitoringStatus(context) {
  return MONITORING_STATUS[context?.status] || context?.status || '未判定'
}
function monitoringValues(frame) {
  const values = Object.values(frame?.values || {})
  if (!values.length) return '无有效原始观测'
  return values.map(value => `${value.label} 均值 ${value.mean} ${value.unit}（${value.count} 条）`).join('；')
}

async function previewFrames() {
  if (!canPreview.value) return
  const ticket = ++request
  const currentSignature = signature.value
  const localController = controller = new AbortController()
  const ids = frameIds.value
  busy.value = 'preview'
  error.value = ''
  notice.value = ''
  preview.value = []
  result.value = null
  try {
    const rows = []
    for (const id of ids) {
      rows.push(await api.videoFrame(id, props.revision, localController.signal))
    }
    if (disposed || localController.signal.aborted || ticket !== request
        || currentSignature !== signature.value) return
    preview.value = ids.map((id, index) => ({
      id,
      image: rows[index].image_base64,
      mediaType: rows[index].media_type,
      frame: rows[index].frame,
    }))
    if (preview.value.some(item => badQuality(item.frame)))
      notice.value = '预览中包含无法读取或人工排除的帧；请调整起点/帧数后重新预览。'
  } catch (e) {
    if (disposed || localController.signal.aborted || ticket !== request
        || currentSignature !== signature.value) return
    error.value = e.message || '帧预览未完成，请稍后重试。'
  } finally {
    if (ticket === request) busy.value = ''
  }
}

async function sendSequence() {
  if (!canSend.value) return
  const ticket = ++request
  const currentSignature = signature.value
  const ids = preview.value.map(item => item.id)
  const localController = controller = new AbortController()
  busy.value = 'send'
  error.value = ''
  notice.value = ''
  result.value = null
  try {
    const response = await api.visionObserve({
      kind: 'video_frames',
      frame_ids: ids,
      video_revision: props.revision,
      question: question.value.trim(),
    }, localController.signal)
    if (disposed || localController.signal.aborted || ticket !== request
        || currentSignature !== signature.value) return
    result.value = response
  } catch (e) {
    if (disposed || localController.signal.aborted || ticket !== request
        || currentSignature !== signature.value) return
    error.value = e.message || '序列视觉观察未完成，请稍后重试。'
  } finally {
    if (ticket === request) busy.value = ''
  }
}

function cancel() {
  if (!busy.value) return
  controller?.abort()
  notice.value = '已停止等待；服务端可能仍在处理，本次结果不会写入项目。'
}

onBeforeUnmount(() => {
  disposed = true
  request++
  controller?.abort()
})
</script>

<template>
  <details class="sequence-evidence" :open="!collapsed" aria-label="短片段视觉证据">
    <summary class="se-summary">
      <span>短片段视觉观察（2—8 帧）</span>
      <span class="se-badge">显式发送 · 先预览</span>
    </summary>
    <div class="se-body">
      <p class="se-note">
        选择起点和帧数后先预览；只有点击“发送序列”才会把预览中的帧按时间顺序交给视觉模型。
        只比较已发送帧，不推断帧间未展示的移动、扩散或排放过程，也不给出浓度或责任结论。
      </p>
      <p v-if="total < 2" class="se-hint">当前视频不足 2 帧，无法进行短片段观察。</p>
      <template v-else>
        <div class="se-controls">
          <label>起始帧（1 基）
            <input v-model.number="start" type="number" :min="1" :max="Math.max(1,total-count+1)" step="1" :disabled="busy" />
          </label>
          <label>帧数
            <select v-model.number="count" :disabled="busy">
              <option v-for="n in maxCount" :key="n" :value="n" :disabled="n<2">{{ n }}</option>
            </select>
          </label>
          <button type="button" :disabled="!canPreview" @click="previewFrames">
            {{ busy==='preview' ? '正在预览…' : '预览选中帧' }}
          </button>
        </div>
        <p class="se-hint">将发送第 {{ frameNumbers[0] }}—{{ frameNumbers[frameNumbers.length-1] }} 帧；按时间顺序排列。</p>
        <p v-if="error" class="se-error" role="alert">{{ error }}</p>
        <p v-if="notice" class="se-notice" role="status">{{ notice }}</p>
        <p v-if="busy==='preview'" class="se-progress" role="status">正在读取每帧图像并核对质量…</p>

        <div v-if="preview.length" class="se-preview" aria-label="待发送帧预览">
          <figure v-for="item in preview" :key="item.id" class="se-frame" :class="{bad:badQuality(item.frame)}">
            <img :src="`data:${item.mediaType};base64,${item.image}`" :alt="`视频第${item.frame.id+1}帧预览`" loading="lazy" />
            <figcaption>
              <b>#{{ item.frame.id+1 }}</b> · {{ item.frame.time_sec ?? '时间无效' }}s ·
              {{ qualityText(item.frame) }}
              <span v-if="badQuality(item.frame)" class="se-bad">不可发送</span>
            </figcaption>
          </figure>
        </div>

        <label class="se-question">观察问题
          <textarea v-model="question" maxlength="500" rows="2" :disabled="busy" />
        </label>
        <div class="se-actions">
          <button type="button" class="primary" :disabled="!canSend" @click="sendSequence">
            {{ busy==='send' ? '正在观察序列…' : '发送序列给视觉模型' }}
          </button>
          <button v-if="busy" type="button" @click="cancel">停止等待</button>
          <button v-if="result" type="button" @click="result=null">清除结果</button>
        </div>
        <p v-if="!preview.length" class="se-hint">请先预览并确认每帧来源与质量；未预览时不能发送。</p>
        <p v-else-if="!sendable" class="se-hint">预览中包含无法读取或人工排除的帧，不能发送；请调整范围后重新预览。</p>
        <p v-if="busy==='send'" class="se-progress" role="status">正在把已预览的 {{ count }} 帧发送给视觉模型…</p>

        <article v-if="result" class="se-result" aria-live="polite">
          <div class="se-result-head">
            <b>序列观察结果</b>
            <span>{{ result.model }} · {{ result.cached ? '本会话缓存' : '新请求' }} · {{ result.elapsed_s }}s</span>
          </div>
          <p class="se-meta">
            帧 {{ result.frame_ids?.join('、') }} · {{ result.time_start_s ?? '—' }}—{{ result.time_end_s ?? '—' }}s
          </p>
          <p v-if="result.static_sequence" class="se-warning">
            所选帧画面相同或几乎相同：只能说明这些帧未见变化，不能推断现场静止或没有污染。
          </p>
          <section v-for="frame in result.frames" :key="frame.index" class="se-result-frame">
            <p class="se-frame-head">
              第 {{ frame.index }} 张 · 帧 #{{ frame.source?.frame_number }} ·
              {{ frame.source?.time_sec ?? '时间无效' }}s · {{ qualityText(frame.source_quality) }}
            </p>
            <p v-if="frame.source_quality?.quality_messages?.length" class="se-quality">
              质量提示：{{ frame.source_quality.quality_messages.join('；') }}
            </p>
            <ul>
              <li v-for="(observation, index) in frame.observations" :key="index">
                {{ observation.visible }}
                <span v-if="observation.where">（{{ observation.where }}）</span>
                <span v-if="observation.uncertainty?.length" class="se-uncertainty">不确定：{{ observation.uncertainty.join('；') }}</span>
              </li>
            </ul>
          </section>
          <div v-if="result.changes?.length" class="se-changes">
            <b>直接可见变化</b>
            <ul>
              <li v-for="(change, index) in result.changes" :key="index">
                第 {{ change.from }} → {{ change.to }} 张：{{ change.visible_change }}
                <span v-if="change.uncertainty?.length" class="se-uncertainty">不确定：{{ change.uncertainty.join('；') }}</span>
              </li>
            </ul>
          </div>
          <section v-if="result.monitoring_context" class="se-monitoring">
            <div class="se-monitoring-head">
              <b>传感器时间窗对照</b>
              <span>{{ monitoringStatus(result.monitoring_context) }}</span>
            </div>
            <p class="se-meta">
              同步：{{ result.monitoring_context.sync_confirmed ? '已人工确认' : '未确认' }}
              · 偏移 {{ result.monitoring_context.time_offset_sec }}s
              · 窗口 {{ result.monitoring_context.window_sec }}s
              <template v-if="result.monitoring_context.data_identity?.label"> · {{ result.monitoring_context.data_identity.label }}</template>
            </p>
            <p class="se-quality">{{ result.monitoring_context.notice }}</p>
            <ul v-if="result.monitoring_context.frames?.length">
              <li v-for="item in result.monitoring_context.frames" :key="item.frame_id">
                帧 #{{ item.frame_number }} · T+{{ item.time_sec }}s · 记录 {{ item.record_count }} 条：
                {{ monitoringValues(item) }}
              </li>
            </ul>
          </section>
          <p v-if="result.not_visible?.length" class="se-gap">未看到/无法判断：{{ result.not_visible.join('；') }}</p>
          <p v-if="result.quality_notes?.length" class="se-quality">模型画面质量提示：{{ result.quality_notes.join('；') }}</p>
          <p class="se-limits">{{ result.limits.join(' ') }}</p>
        </article>
      </template>
    </div>
  </details>
</template>

<style scoped>
.sequence-evidence{margin:14px 0;padding:14px;border:1px solid var(--c-border);border-radius:9px;background:var(--c-surface);color:var(--c-sub);font-size:14px;line-height:1.75}
.se-summary{display:flex;flex-wrap:wrap;align-items:center;gap:10px;cursor:pointer;color:var(--c-ink);font-weight:600;font-size:16px}
.se-badge{padding:1px 7px;border:1px solid #34566a;border-radius:10px;color:var(--c-accent);font-size:12px;font-weight:400}
.se-body{margin-top:8px}
.se-note{margin:0 0 10px}
.se-controls{display:flex;flex-wrap:wrap;align-items:end;gap:10px}
.se-controls label,.se-question{display:flex;flex-direction:column;gap:5px;color:var(--c-sub)}
.se-controls input,.se-controls select{font:inherit;font-size:15px;min-height:38px;padding:6px 8px;border:1px solid var(--c-border);border-radius:6px;background:var(--c-surface-2);color:var(--c-ink)}
.se-controls button,.se-actions button{font:inherit;font-size:14px;min-height:36px;padding:7px 12px;border:1px solid var(--c-border);border-radius:6px;background:transparent;color:var(--c-ink);cursor:pointer}
.se-controls button:disabled,.se-actions button:disabled{opacity:.45;cursor:not-allowed}
.se-question{margin:10px 0}
.se-question textarea{width:100%;box-sizing:border-box;font:inherit;font-size:15px;line-height:1.6;padding:8px 10px;border:1px solid var(--c-border);border-radius:6px;background:var(--c-surface-2);color:var(--c-ink);resize:vertical}
.se-actions{display:flex;flex-wrap:wrap;gap:8px}
.se-actions button.primary{background:var(--c-accent);border-color:var(--c-accent);color:#06121c;font-weight:600}
.se-hint,.se-notice,.se-progress{margin:8px 0 0;font-size:13px}
.se-notice,.se-warning,.se-uncertainty{color:#e8c98a}
.se-error{margin:8px 0 0;padding:7px 10px;border-left:2px solid #a04458;background:#2a1419;color:#ffbc9d;border-radius:4px}
.se-preview{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,120px),1fr));gap:8px;margin:10px 0}
.se-frame{margin:0;padding:6px;border:1px solid var(--c-border);border-radius:6px;background:var(--c-surface-2)}
.se-frame.bad{border-color:#a04458}
.se-frame img{display:block;width:100%;height:auto;max-height:110px;object-fit:contain;background:#101c25;border-radius:3px}
.se-frame figcaption{font-size:12px;margin-top:4px;overflow-wrap:anywhere}
.se-bad{color:#ffbc9d;display:block}
.se-result{margin-top:12px;padding:12px;border:1px solid var(--c-border);border-radius:7px;background:var(--c-surface-2)}
.se-result-head{display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px;color:var(--c-ink)}
.se-result-head span{font-size:13px;color:var(--c-sub)}
.se-meta{font-size:13px;margin:4px 0 8px}
.se-result-frame{margin:10px 0;padding-top:8px;border-top:1px solid var(--c-border)}
.se-frame-head{margin:0 0 4px;color:var(--c-ink)}
.se-quality{font-size:13px;color:var(--c-sub)}
.se-result-frame ul,.se-changes ul,.se-monitoring ul{margin:4px 0;padding-left:20px}
.se-monitoring{margin:10px 0;padding:8px 10px;border:1px solid var(--c-border);border-radius:6px;background:var(--c-surface)}
.se-monitoring-head{display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px;color:var(--c-ink)}
.se-monitoring-head span{font-size:13px;color:var(--c-sub)}
.se-uncertainty{font-size:13px;margin-left:4px}
.se-gap,.se-limits{margin:8px 0 0;font-size:13px}
.se-limits{padding-top:8px;border-top:1px solid var(--c-border);color:#e8c98a}
@media(max-width:600px){.sequence-evidence{padding:10px}.se-actions button,.se-controls button{min-height:40px}}
</style>
