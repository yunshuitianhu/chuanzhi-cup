<script setup>
/**
 * Q18 阶段 A：单图/单帧视觉证据。
 *
 * 只有用户点击“发送”才会把当前图片交给视觉模型；选择来源、切换帧或页面挂载
 * 都不会自动发送。结果只在本组件内展示，不写入报告/排查板。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../api/client'
import { useSessionStore } from '../stores/session'

const props = defineProps({
  context: { type: Object, default: null },
  disabled: Boolean,
  collapsed: Boolean,
})
const store = useSessionStore()

const question = ref('画面中是否能看到漂浮物、泡沫、排口或明显水面异常？看不清、被遮挡或无法判断的请说明。')
const busy = ref(false)
const error = ref('')
const notice = ref('')
const result = ref(null)
const visualKinds = ['video_frame', 'image_asset']
const isVisual = computed(() => visualKinds.includes(props.context?.kind))
const selection = computed(() => (props.context?.selection && typeof props.context.selection === 'object')
  ? props.context.selection : {})
const sourceLabel = computed(() => {
  if (!isVisual.value) return ''
  if (props.context?.title) return props.context.title
  if (props.context?.kind === 'video_frame') return `视频第 ${Number(selection.value.frame_id) + 1} 帧`
  return '当前图片附件'
})
const signature = computed(() => JSON.stringify({
  kind: props.context?.kind,
  selection: selection.value,
  workspace: store.workspaceRevision,
  project: store.projectState.current?.id ?? null,
  analysis: store.status.analysis_revision,
  results: store.status.results_revision,
}))
const selectionValid = computed(() => {
  if (!isVisual.value) return false
  if (props.context.kind === 'video_frame')
    return Number.isInteger(selection.value.frame_id)
      && typeof selection.value.video_revision === 'string'
      && selection.value.video_revision.length === 64
  return typeof selection.value.asset_id === 'string'
    && typeof selection.value.asset_revision === 'string'
    && selection.value.asset_revision.length === 64
})
const canSend = computed(() => selectionValid.value && !props.disabled && !busy.value
  && store.ready && !store.loading && !store.switchingProject)

let request = 0
let controller = null
let disposed = false

watch(signature, () => {
  request++
  controller?.abort()
  controller = null
  busy.value = false
  result.value = null
  error.value = ''
  notice.value = ''
})

function payload() {
  const base = { kind: props.context.kind, question: question.value.trim() }
  if (props.context.kind === 'video_frame') return {
    ...base, frame_id: selection.value.frame_id,
    video_revision: selection.value.video_revision,
  }
  return { ...base, asset_id: selection.value.asset_id,
    asset_revision: selection.value.asset_revision }
}

async function send() {
  if (!canSend.value) return
  const ticket = ++request
  const currentSignature = signature.value
  const localController = controller = new AbortController()
  busy.value = true
  error.value = ''
  notice.value = ''
  result.value = null
  try {
    const response = await api.visionObserve(payload(), localController.signal)
    if (disposed || ticket !== request || currentSignature !== signature.value) return
    result.value = response
  } catch (e) {
    if (disposed || localController.signal.aborted || ticket !== request
        || currentSignature !== signature.value) return
    error.value = e.message || '视觉观察未完成，请稍后重试。'
  } finally {
    if (ticket === request) busy.value = false
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
  <details v-if="isVisual" class="visual-evidence" :open="!collapsed" aria-label="视觉证据观察">
    <summary class="ve-summary">
      <span>视觉观察（辅助证据）</span>
      <span class="ve-badge">显式发送</span>
    </summary>
    <div class="ve-body">
    <p class="ve-note">
      选择来源本身不会发送图片；点击下方按钮后，服务器才会读取当前项目中的这张图片并交给视觉模型。
      结果只描述画面可见内容，不给出浓度，也不确认污染源或责任。
    </p>
    <p class="ve-source">
      将发送：<b>{{ sourceLabel }}</b>
      <span v-if="context.kind === 'video_frame'"> · 视频版本 {{ String(selection.video_revision || '未提供').slice(0, 12) }}</span>
      <span v-else> · 附件版本 {{ String(selection.asset_revision || '未提供').slice(0, 12) }}</span>
    </p>
    <label class="ve-question">观察问题
      <textarea v-model="question" maxlength="500" rows="2" :disabled="busy"
        placeholder="例如：画面中是否有漂浮物或泡沫？看不清的请说明。" />
    </label>
    <div class="ve-actions">
      <button type="button" class="primary" :disabled="!canSend" @click="send">
        {{ busy ? '正在观察…' : result ? '重新观察当前图片' : '发送当前图片给视觉模型' }}
      </button>
      <button v-if="busy" type="button" @click="cancel">停止等待</button>
      <button v-if="result" type="button" @click="result = null">清除结果</button>
    </div>
    <p v-if="!canSend && !busy" class="ve-hint">
      需先选择带有效版本号的视频帧或图片；若当前来源已变化，请重新选择。
    </p>
    <p v-if="error" class="ve-error" role="alert">{{ error }}</p>
    <p v-if="notice" class="ve-notice" role="status">{{ notice }}</p>
    <p v-if="busy" class="ve-progress" role="status">正在读取当前图片并等待视觉模型…</p>

    <article v-if="result" class="ve-result" aria-live="polite">
      <div class="ve-result-head">
        <b>观察结果</b>
        <span>{{ result.model }} · {{ result.cached ? '本会话缓存' : '新请求' }} · {{ result.elapsed_s }}s</span>
      </div>
      <p class="ve-meta">
        来源：{{ result.source?.label || sourceLabel }} · 图片哈希
        {{ String(result.source?.sha256 || '').slice(0, 12) }}…
        <template v-if="result.source?.frame_number"> · 第 {{ result.source.frame_number }} 帧 · {{ result.source.time_sec }}s</template>
      </p>
      <ul v-if="result.observations?.length" class="ve-observations">
        <li v-for="(item, index) in result.observations" :key="index">
          <p>{{ item.visible }}</p>
          <p v-if="item.where" class="ve-where">位置：{{ item.where }}</p>
          <p v-if="item.uncertainty?.length" class="ve-uncertainty">不确定：{{ item.uncertainty.join('；') }}</p>
        </li>
      </ul>
      <p v-else class="ve-empty">模型没有形成可核对的可见观察。</p>
      <p v-if="result.not_visible?.length" class="ve-gap">
        未看到/无法判断：{{ result.not_visible.join('；') }}
      </p>
      <p v-if="result.quality_notes?.length" class="ve-quality">
        画面质量提示：{{ result.quality_notes.join('；') }}
      </p>
      <p class="ve-limits">{{ result.limits.join(' ') }}</p>
    </article>
    </div>
  </details>
</template>

<style scoped>
.visual-evidence{margin:14px 0;padding:14px;border:1px solid var(--c-border);border-radius:9px;background:var(--c-surface);color:var(--c-sub);font-size:14px;line-height:1.75}
.ve-summary{display:flex;flex-wrap:wrap;align-items:center;gap:10px;cursor:pointer;color:var(--c-ink);font-weight:600;font-size:16px}
.ve-summary::-webkit-details-marker{margin-right:2px}
.ve-body{margin-top:8px}
.ve-badge{padding:1px 7px;border:1px solid #34566a;border-radius:10px;color:var(--c-accent);font-size:12px;font-weight:400}
.ve-note{margin:0 0 8px}
.ve-source{margin:0 0 10px;color:var(--c-ink)}
.ve-question{display:flex;flex-direction:column;gap:6px;margin:0 0 10px;color:var(--c-sub)}
.ve-question textarea{width:100%;box-sizing:border-box;font:inherit;font-size:15px;line-height:1.6;padding:8px 10px;border:1px solid var(--c-border);border-radius:6px;background:var(--c-surface-2);color:var(--c-ink);resize:vertical}
.ve-actions{display:flex;flex-wrap:wrap;gap:8px}
.ve-actions button{font:inherit;font-size:14px;min-height:36px;padding:7px 12px;border:1px solid var(--c-border);border-radius:6px;background:transparent;color:var(--c-ink);cursor:pointer}
.ve-actions button.primary{background:var(--c-accent);border-color:var(--c-accent);color:#06121c;font-weight:600}
.ve-actions button:disabled{opacity:.45;cursor:not-allowed}
.ve-hint,.ve-notice,.ve-progress{margin:8px 0 0;font-size:13px}
.ve-notice{color:#e8c98a}
.ve-error{margin:8px 0 0;padding:7px 10px;border-left:2px solid #a04458;background:#2a1419;color:#ffbc9d;border-radius:4px}
.ve-result{margin-top:12px;padding:12px;border:1px solid var(--c-border);border-radius:7px;background:var(--c-surface-2)}
.ve-result-head{display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px;margin-bottom:6px;color:var(--c-ink)}
.ve-result-head span{font-size:13px;color:var(--c-sub)}
.ve-meta{margin:0 0 8px;font-size:13px;overflow-wrap:anywhere}
.ve-observations{margin:0;padding-left:20px}
.ve-observations li{margin:0 0 8px}
.ve-observations p{margin:0}
.ve-where,.ve-uncertainty{font-size:13px;color:var(--c-sub)}
.ve-uncertainty{color:#e8c98a}
.ve-empty,.ve-gap,.ve-quality{margin:6px 0 0}
.ve-limits{margin:10px 0 0;padding-top:8px;border-top:1px solid var(--c-border);font-size:13px;color:#e8c98a}
@media(max-width:600px){.visual-evidence{padding:10px}.ve-actions button{min-height:40px}}
</style>
