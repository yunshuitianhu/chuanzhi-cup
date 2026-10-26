<script setup lang='ts'>
import {computed, nextTick, onBeforeUnmount, reactive, ref, watch} from 'vue'
import {useRouter} from 'vue-router'
import {api} from '../api/client'
import {useSessionStore} from '../stores/session'
import {questionContextLocation} from '../utils/questionContext'
import type {AnalysisCard, AnalysisCardPreviewResponse, AnalysisCardRefKind, AnalysisCardsResponse, AnalysisCardsStatus, AnalysisCardSuggestion, InvestigationDraft, InvestigationRetest, ResolvedRef} from '../types/analysisCards'

interface Scope {bundleId: string; projectId: string; workspaceRevision: string; analysisRevision: string; resultsRevision: string; chatRunning: boolean}
interface InlineState {loading: boolean; open: boolean; text: string; error: string}
interface EditorState {title: string; hypothesis: string; supportingNotes: string; opposingNotes: string; location: string; indicators: string[]; plan: string}
interface PreviewState {cardId: string; previewId: string; notice: string; scope: Scope; base: InvestigationDraft; editor: EditorState; submitting: boolean; blocked: boolean; error: string; request: {hash: string; id: string} | null}
interface RefGroup {key: 'support' | 'oppose'; title: string; refs: ResolvedRef[]; status?: 'provided' | 'not_provided'}
type PanelState = 'idle' | 'loading' | 'failed' | 'empty' | AnalysisCardsStatus

const props = defineProps<{bundleId: string}>()
const router = useRouter()
const store = useSessionStore()

const panelId = computed(() => 'analysis-cards-' + props.bundleId)
const INDICATORS = {ammonia:'氨氮',do:'溶解氧',cod:'COD',ph:'pH',turbidity:'浊度',sd:'透明度',orp:'ORP',flow:'流量/流向',connection:'入河连接'}
const open = ref(false)
const loaded = ref(false)
const loading = ref(false)
const loadError = ref('')
const response = ref<AnalysisCardsResponse | null>(null)
const staleNotice = ref('')
const panelNotice = ref('')
const confirmed = ref<Set<string>>(new Set())
const previewLoadingFor = ref('')
const cardErrors = ref<Record<string, string>>({})
const preview = ref<PreviewState | null>(null)
const inline = reactive<Record<string, InlineState>>({})

const EMPTY_INLINE: InlineState = {loading: false, open: false, text: '', error: ''}
const KIND_LABELS: Record<AnalysisCardRefKind, string> = {fact: '事实', record: '记录', rag: '知识库片段', tool: '工具输出', video_frame: '视频帧'}
const REF_KINDS: AnalysisCardRefKind[] = ['fact', 'record', 'rag', 'tool', 'video_frame']

let loadToken = 0
let previewToken = 0
let disposed = false

function str(value: unknown): string { return typeof value === 'string' ? value : '' }
function num(value: unknown): number | undefined { return typeof value === 'number' && Number.isFinite(value) ? value : undefined }
function strList(value: unknown): string[] { return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [] }
function truncate(value: string, max: number): string { return value.length > max ? value.slice(0, max) + '…（已截断显示）' : value }
function messageOf(error: unknown): string {
  if (typeof error === 'string' && error) return error
  const detail = error as {message?: unknown; detail?: unknown} | null
  if (detail && typeof detail.message === 'string' && detail.message) return detail.message
  if (detail && typeof detail.detail === 'string' && detail.detail) return detail.detail
  return '请求失败，请稍后重试'
}
function toPlainText(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  try { return JSON.stringify(value, null, 2) ?? '' } catch { return String(value) }
}

function currentScope(): Scope {
  return {
    bundleId: props.bundleId,
    projectId: String(store.projectState?.current?.id ?? ''),
    workspaceRevision: String(store.workspaceRevision ?? ''),
    analysisRevision: String(store.status?.analysis_revision ?? ''),
    resultsRevision: String(store.status?.results_revision ?? ''),
    chatRunning: Boolean(store.chatRunning),
  }
}
function sameScope(left: Scope, right: Scope): boolean {
  return left.bundleId === right.bundleId && left.projectId === right.projectId && left.workspaceRevision === right.workspaceRevision && left.analysisRevision === right.analysisRevision && left.resultsRevision === right.resultsRevision && left.chatRunning === right.chatRunning
}
const canQuery = computed(() => store.ready === true && store.loading !== true && store.switchingProject !== true && store.chatRunning !== true)
const scopeKey = computed(() => {
  const scope = currentScope()
  return [scope.bundleId, scope.projectId, scope.workspaceRevision, scope.analysisRevision, scope.resultsRevision, scope.chatRunning ? 'run' : 'idle', store.switchingProject ? 'switching' : 'idle'].join('|')
})

function normalizeRef(ref: ResolvedRef): ResolvedRef | null {
  if (!ref || !REF_KINDS.includes(ref.kind)) return null
  return {
    kind: ref.kind,
    label: str(ref.label),
    fact_id: str(ref.fact_id) || undefined,
    position: num(ref.position),
    map_sample_id: num(ref.map_sample_id),
    evidence_id: str(ref.evidence_id) || undefined,
    tool_id: str(ref.tool_id) || undefined,
    frame_id: num(ref.frame_id),
    video_revision: str(ref.video_revision) || undefined,
    excerpt: truncate(str(ref.excerpt), 1200),
    source_url: str(ref.source_url) || null,
    notice: str(ref.notice),
  }
}
function normalizeSuggestion(raw: Partial<AnalysisCardSuggestion> | null | undefined): AnalysisCardSuggestion {
  const retest = (raw?.retest ?? {}) as Partial<InvestigationRetest>
  return {
    title: str(raw?.title),
    hypothesis: str(raw?.hypothesis),
    supporting_notes: str(raw?.supporting_notes),
    opposing_notes: str(raw?.opposing_notes),
    retest: {location: str(retest.location), latitude: null, longitude: null, indicators: strList(retest.indicators), plan: str(retest.plan)},
  }
}
function normalizeCard(raw: AnalysisCard): AnalysisCard | null {
  if (!raw || typeof raw.id !== 'string' || typeof raw.title !== 'string' || typeof raw.finding !== 'string') return null
  const support = (Array.isArray(raw.support) ? raw.support : []).map(normalizeRef).filter((ref): ref is ResolvedRef => ref !== null)
  const oppose = (Array.isArray(raw.oppose) ? raw.oppose : []).map(normalizeRef).filter((ref): ref is ResolvedRef => ref !== null)
  return {id: raw.id, confirmed_task_id: str(raw.confirmed_task_id)||null, title: raw.title, finding: raw.finding, support, oppose, gaps: strList(raw.gaps), opposing_evidence_status: raw.opposing_evidence_status === 'provided' ? 'provided' : 'not_provided', suggestion: normalizeSuggestion(raw.suggestion as Partial<AnalysisCardSuggestion> | undefined)}
}
function normalize(payload: unknown): AnalysisCardsResponse {
  const raw = (payload ?? {}) as Partial<AnalysisCardsResponse>
  const status: AnalysisCardsStatus = raw.status === 'ready' || raw.status === 'stale' || raw.status === 'invalid' ? raw.status : 'unavailable'
  const rawCards = status === 'ready' && Array.isArray(raw.cards) ? raw.cards : []
  if (raw.version !== 'analysis-cards-v1') return {version:'analysis-cards-v1', status:'invalid', notice:'卡片格式版本不受支持，请重新分析。', cards:[]}
  return {version: 'analysis-cards-v1', status, notice: str(raw.notice), cards: rawCards.map(normalizeCard).filter((card): card is AnalysisCard => card !== null)}
}

async function loadCards(): Promise<void> {
  if (!canQuery.value || loading.value) return
  const token = ++loadToken
  const scope = currentScope()
  loading.value = true
  loadError.value = ''
  try {
    const data = (await api.analysisCards(props.bundleId)) as AnalysisCardsResponse
    if (disposed || token !== loadToken) return
    if (!sameScope(scope, currentScope())) { invalidate('分析状态已更新，正在重新加载研判卡片。'); return }
    response.value = normalize(data)
    confirmed.value = new Set(response.value.cards.filter(card=>card.confirmed_task_id).map(card=>card.id))
    loaded.value = true
  } catch (error) {
    if (disposed || token !== loadToken) return
    if (!sameScope(scope, currentScope())) return
    loadError.value = messageOf(error)
  } finally {
    if (token === loadToken) loading.value = false
  }
}
function scheduleLoad(): void {
  nextTick(() => { if (!disposed && open.value && !loaded.value && !loading.value) void loadCards() })
}
function invalidate(message = ''): void {
  loadToken += 1
  previewToken += 1
  loading.value = false
  previewLoadingFor.value = ''
  Object.keys(inline).forEach(key => { delete inline[key] })
  const state = preview.value
  const now = currentScope()
  // 同项目分析更新时保留用户编辑；切换项目则清除上一项目的本地显示。
  if (state && state.scope.bundleId === now.bundleId && state.scope.projectId === now.projectId
      && state.scope.workspaceRevision === now.workspaceRevision) {
    state.blocked = true
    state.error = '分析状态已变化，草稿已保留但不可保存。请复制所需内容，取消后重新生成并核对。'
    staleNotice.value = message || '当前卡片为旧状态快照；请取消草稿后重新载入。'
    return
  }
  preview.value = null
  loaded.value = false
  response.value = null
  loadError.value = ''
  staleNotice.value = message
  confirmed.value = new Set()
  panelNotice.value = ''
  cardErrors.value = {}
  if (open.value) scheduleLoad()
}
function toggle(): void {
  open.value = !open.value
  if (open.value) { staleNotice.value = ''; scheduleLoad() }
}

const cards = computed<AnalysisCard[]>(() => (response.value && response.value.status === 'ready' ? response.value.cards : []))
const panelState = computed<PanelState>(() => {
  if (loading.value) return 'loading'
  if (loadError.value) return 'failed'
  if (!response.value) return 'idle'
  if (response.value.status !== 'ready') return response.value.status
  return response.value.cards.length ? 'ready' : 'empty'
})
const showPanelAlert = computed(() => panelState.value !== 'ready')
const panelMessage = computed(() => {
  const current = response.value
  if (loadError.value) return '加载失败：' + loadError.value
  if (loading.value) return '正在加载本次运行的研判卡片…'
  if (!current) return '展开后按需加载；只显示本次运行的真实工具轨迹。'
  if (current.status === 'unavailable') return current.notice || '本次运行不提供研判卡片（未成功完成或未提交）。'
  if (current.status === 'stale') return current.notice || '研判卡片与当前分析结果不一致，已标记为过期。'
  if (current.status === 'invalid') return current.notice || '研判卡片未通过校验，内容已隐藏。'
  if (!current.cards.length) return current.notice || '本次分析未生成研判卡片。'
  return current.notice
})
const headingMeta = computed(() => {
  const current = response.value
  if (loading.value) return '加载中…'
  if (current && current.status === 'ready') return current.cards.length + ' 张 · AI 辅助建议，需核对来源'
  if (current) return '不可用'
  return '按需加载'
})

function refKey(ref: ResolvedRef): string {
  return [ref.kind, ref.fact_id ?? '', ref.tool_id ?? '', ref.frame_id ?? '', ref.evidence_id ?? '', ref.position ?? ''].join(':')
}
function inlineFor(ref: ResolvedRef): InlineState { return inline[refKey(ref)] ?? EMPTY_INLINE }
function ensureInline(ref: ResolvedRef): InlineState {
  const key = refKey(ref)
  const existing = inline[key]
  if (existing) return existing
  const created: InlineState = {loading: false, open: false, text: '', error: ''}
  inline[key] = created
  return inline[key]  // 通过 reactive 代理更新，异步返回时才能触发模板重新渲染。
}
async function toggleInline(ref: ResolvedRef): Promise<void> {
  const state = ensureInline(ref)
  if (state.open) { state.open = false; return }
  state.open = true
  if (state.loading || state.text) return
  const scope = currentScope()
  state.loading = true
  state.error = ''
  try {
    let result: unknown = null
    if (ref.kind === 'fact' && ref.fact_id) result = await api.evidenceRows(props.bundleId, ref.fact_id)
    else if ((ref.kind === 'tool' || ref.kind === 'rag') && ref.tool_id) result = await api.evidenceTool(props.bundleId, ref.tool_id)
    if (result === null) { state.error = '该引用缺少可查询的标识'; return }
    if (!sameScope(scope, currentScope())) { state.open = false; state.error = '分析状态已变化，请刷新后重试'; return }
    state.text = truncate(toPlainText(result), 4000)
  } catch (error) {
    state.error = messageOf(error)
  } finally {
    state.loading = false
  }
}
async function openVideoFrame(ref: ResolvedRef): Promise<void> {
  const frameId = ref.frame_id, analysisRevision = store.status.analysis_revision
  if (typeof frameId !== 'number' || !Number.isInteger(frameId) || frameId<0 || !ref.video_revision) return
  const state = ensureInline(ref)
  if (state.loading) return
  if (typeof analysisRevision !== 'number') { state.error='数据版本尚未就绪，请刷新后重试。'; state.open=true; return }
  const scope = currentScope()
  state.loading = true
  state.error = ''
  try {
    const context = await api.createQuestionContext({kind: 'video_frame', frame_id: frameId, video_revision: ref.video_revision,
      project_id: store.projectState.current?.id??null, analysis_revision: analysisRevision, results_revision:store.status.results_revision??0})
    if (!sameScope(scope, currentScope())) { state.error = '分析状态已变化，请刷新后重试'; state.open = true; return }
    const target = questionContextLocation(context)
    if (!target) throw new Error('视频来源格式无效，请重新选择。')
    await router.push(target)
  } catch (error) {
    state.error = messageOf(error)
    state.open = true
  } finally {
    state.loading = false
  }
}
function recordRoute(ref: ResolvedRef): string | null {
  const url = ref.source_url
  if (ref.kind !== 'record' || typeof url !== 'string') return null
  const head = '/charts?evidence='
  const tail = '#trace-map'
  if (!url.startsWith(head) || !url.endsWith(tail)) return null
  const body = url.slice(head.length, url.length - tail.length)
  const split = body.indexOf('&row=')
  if (split <= 0) return null
  const rowText = body.slice(split + '&row='.length)
  if (!rowText || !/^[0-9]+$/.test(rowText) || !Number.isInteger(ref.position) || String(ref.position)!==rowText) return null
  let evidence = ''
  try { evidence = decodeURIComponent(body.slice(0, split)) } catch { return null }
  if (evidence !== props.bundleId) return null
  return url
}
function openRoute(url: string | null): void { if (url) void router.push(url) }
function kindLabel(kind: AnalysisCardRefKind): string { return KIND_LABELS[kind] ?? '引用' }
function isFact(ref: ResolvedRef): boolean { return ref.kind === 'fact' }
function isTool(ref: ResolvedRef): boolean { return ref.kind === 'tool' }
function isVideo(ref: ResolvedRef): boolean { return ref.kind === 'video_frame' }
function isRag(ref: ResolvedRef): boolean { return ref.kind === 'rag' }
function isRecord(ref: ResolvedRef): boolean { return ref.kind === 'record' }
function refGroups(card: AnalysisCard): RefGroup[] {
  return [
    {key: 'support', title: '支持证据', refs: card.support},
    {key: 'oppose', title: '反向证据', refs: card.oppose, status: card.opposing_evidence_status},
  ]
}
function opposeLabel(status: 'provided' | 'not_provided' | undefined): string {
  return status === 'provided' ? '已提供反向证据' : '未提供反向证据（不代表已排除矛盾）'
}
function hasSuggestion(card: AnalysisCard): boolean {
  const suggestion = card.suggestion
  return Boolean(suggestion.title || suggestion.hypothesis || suggestion.retest.location || suggestion.retest.plan || suggestion.retest.indicators.length)
}

function editorFromDraft(draft: InvestigationDraft): EditorState {
  return {
    title: draft.title,
    hypothesis: draft.hypothesis,
    supportingNotes: draft.supporting_notes,
    opposingNotes: draft.opposing_notes,
    location: draft.retest.location,
    indicators: [...draft.retest.indicators],
    plan: draft.retest.plan,
  }
}
function normalizeDraft(raw: InvestigationDraft): InvestigationDraft {
  const source = (raw ?? {}) as InvestigationDraft
  const retest = (source.retest ?? {}) as InvestigationRetest
  return {
    title: str(source.title),
    hypothesis: str(source.hypothesis),
    supporting_notes: str(source.supporting_notes),
    opposing_notes: str(source.opposing_notes),
    status: 'open',
    retest: {
      location: str(retest.location),
      latitude: num(retest.latitude) ?? null,
      longitude: num(retest.longitude) ?? null,
      indicators: strList(retest.indicators),
      plan: str(retest.plan),
      observed_at: '',
      result: '',
    },
    review: {outcome: '', reviewer: '', notes: ''},
  }
}
function draftFromEditor(state: PreviewState): InvestigationDraft {
  const base = state.base
  const indicators = [...state.editor.indicators]
  return {
    ...base,
    title: state.editor.title.trim(),
    hypothesis: state.editor.hypothesis.trim(),
    supporting_notes: state.editor.supportingNotes.trim(),
    opposing_notes: state.editor.opposingNotes.trim(),
    status: 'open',
    retest: {...base.retest, location: state.editor.location.trim(), indicators, plan: state.editor.plan.trim(), observed_at: '', result: ''},
    review: {outcome: '', reviewer: '', notes: ''},
  }
}
function coordinateText(retest: InvestigationRetest): string {
  const latitude = retest.latitude
  const longitude = retest.longitude
  if (typeof latitude === 'number' && typeof longitude === 'number' && Number.isFinite(latitude) && Number.isFinite(longitude)) return latitude.toFixed(6) + ', ' + longitude.toFixed(6)
  return '未提供坐标（不显示模型建议的位置）'
}
function validate(state: PreviewState): string {
  const editor = state.editor
  if (!editor.title.trim()) return '请填写任务标题'
  if (editor.title.trim().length > 120) return '标题不能超过 120 个字符'
  if (!editor.hypothesis.trim()) return '请填写复测假设'
  if (editor.hypothesis.length > 2000) return '复测假设不能超过 2000 个字符'
  if (editor.supportingNotes.length > 4000 || editor.opposingNotes.length > 4000) return '备注不能超过 4000 个字符'
  if (editor.location.length > 300) return '地点不能超过 300 个字符'
  if (editor.plan.length > 3000) return '复测计划不能超过 3000 个字符'
  if (editor.indicators.length > 9 || new Set(editor.indicators).size !== editor.indicators.length
      || editor.indicators.some(item => !Object.hasOwn(INDICATORS,item))) return '请选择有效且不重复的复测指标'
  return ''
}
function stableStringify(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value) ?? 'null'
  if (Array.isArray(value)) return '[' + value.map(item => stableStringify(item)).join(',') + ']'
  const record = value as Record<string, unknown>
  return '{' + Object.keys(record).sort().map(key => JSON.stringify(key) + ':' + stableStringify(record[key])).join(',') + '}'
}
function newRequestId(): string {
  const cryptoApi = globalThis.crypto
  if (cryptoApi && typeof cryptoApi.randomUUID === 'function') return cryptoApi.randomUUID()
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, marker => {
    const random = Math.floor(Math.random() * 16)
    return (marker === 'x' ? random : (random & 0x3) | 0x8).toString(16)
  })
}
function canStartPreview(): boolean { return canQuery.value && !preview.value && !previewLoadingFor.value && panelState.value === 'ready' }
async function startPreview(card: AnalysisCard): Promise<void> {
  if (!canQuery.value || previewLoadingFor.value || panelState.value !== 'ready' || confirmed.value.has(card.id)) return
  if (preview.value) { panelNotice.value = '请先确认或取消当前复测草稿。'; return }
  const scope = currentScope()
  const token = ++previewToken
  previewLoadingFor.value = card.id
  cardErrors.value = {...cardErrors.value, [card.id]: ''}
  try {
    const data = (await api.previewAnalysisCard(props.bundleId, card.id)) as AnalysisCardPreviewResponse
    if (disposed || token !== previewToken) return
    if (!sameScope(scope, currentScope())) { staleNotice.value = '分析状态已变化，请重新生成复测草稿。'; return }
    if (data.bundle_id !== props.bundleId || data.card_id !== card.id || !data.preview_id) {
      cardErrors.value = {...cardErrors.value, [card.id]: '复测草稿与当前卡片不匹配，请重试。'}
      return
    }
    const base = normalizeDraft(data.draft)
    preview.value = {cardId: card.id, previewId: data.preview_id, notice: str(data.notice), scope, base, editor: editorFromDraft(base), submitting: false, blocked: false, error: '', request: null}
  } catch (error) {
    if (disposed || token !== previewToken) return
    cardErrors.value = {...cardErrors.value, [card.id]: '生成草稿失败：' + messageOf(error)}
  } finally {
    if (token === previewToken) previewLoadingFor.value = ''
  }
}
function cancelPreview(): void {
  previewToken += 1
  preview.value = null
  previewLoadingFor.value = ''
  if (staleNotice.value) invalidate()
}
async function submitConfirm(): Promise<void> {
  const state = preview.value
  if (!state || state.submitting || state.blocked) return
  const problem = validate(state)
  if (problem) { state.error = problem; return }
  if (!sameScope(state.scope, currentScope())) {
    state.blocked = true
    state.error = '工作区或分析状态已变化，无法保存。请取消后重新生成草稿；当前编辑内容仍保留在表单中。'
    return
  }
  const draft = draftFromEditor(state)
  const hash = stableStringify({preview_id: state.previewId, draft})
  if (!state.request || state.request.hash !== hash) state.request = {hash, id: newRequestId()}
  const requestId = state.request.id
  state.submitting = true
  state.error = ''
  try {
    const result = (await store.confirmAnalysisCard(props.bundleId, state.cardId, {preview_id: state.previewId, request_id: requestId, draft})) as {refresh_warning?: string; notice?:string; persisted_project?:boolean} | null
    if (disposed || !result || preview.value !== state || !sameScope(state.scope, currentScope())) return
    const warning = result && typeof result.refresh_warning === 'string' ? result.refresh_warning : ''
    confirmed.value = new Set([...confirmed.value, state.cardId])
    panelNotice.value = (result.notice || (result.persisted_project ? '已确认并保存到项目排查板。' : '已加入临时会话排查板，请保存项目以便恢复。')) + (warning ? ' ' + warning : '')
    if (preview.value === state) { preview.value = null; previewToken += 1 }
  } catch (error) {
    if (!disposed && preview.value === state && sameScope(state.scope, currentScope())) state.error = '保存失败（未确认已写入）：' + messageOf(error)
  } finally {
    if (preview.value === state) state.submitting = false
  }
}

watch(() => props.bundleId, () => {
  confirmed.value = new Set()
  panelNotice.value = ''
  staleNotice.value = ''
  cardErrors.value = {}
  Object.keys(inline).forEach(key => { delete inline[key] })
  invalidate()
})
watch(scopeKey, (next, previous) => { if (next !== previous) invalidate('项目或分析状态已变化，请核对当前卡片。') })
watch(canQuery, value => { if (value && open.value && !loaded.value && !preview.value) scheduleLoad() })
onBeforeUnmount(() => { disposed = true; loadToken += 1; previewToken += 1 })
</script>

<template>
  <section class='ac' :data-state='panelState' :aria-busy='loading'>
    <button type='button' class='ac-toggle' :aria-expanded='open' :aria-controls='panelId' @click='toggle'>
      <span class='ac-heading'>研判卡片与建议操作</span>
      <span class='ac-meta'>{{ headingMeta }}</span>
      <span class='ac-chevron' aria-hidden='true'>{{ open ? '−' : '+' }}</span>
    </button>
    <div v-if='open' :id='panelId' class='ac-body' role='region' aria-label='研判卡片与建议操作'>
      <p v-if='staleNotice' class='ac-alert' role='status'>{{ staleNotice }}</p>
      <p v-if='panelNotice' class='ac-alert ac-alert--ok' role='status'>{{ panelNotice }}</p>
      <p v-if='showPanelAlert' class='ac-alert' :data-tone='panelState' role='status'>{{ panelMessage }}</p>
      <button v-if="panelState==='failed'" class='ac-secondary' type='button' :disabled='!canQuery' @click='loadCards'>重试加载卡片</button>
      <p v-if="panelState==='ready' && panelMessage" class='ac-note'>{{ panelMessage }}</p>
      <article v-for='card in cards' :key='card.id' class='ac-card'>
        <header class='ac-card-head'>
          <h3 class='ac-title'>{{ card.title }}</h3>
          <span class='ac-badge'>{{ confirmed.has(card.id) ? '已加入排查板' : 'AI 辅助建议 · 未确认' }}</span>
        </header>
        <p class='ac-finding'>{{ card.finding }}</p>
        <details v-for='group in refGroups(card)' :key='group.key' class='ac-group' :open="group.key==='oppose'">
          <summary class='ac-group-title'>{{ group.title }}<span class='ac-count'>{{ group.refs.length }}</span></summary>
          <p v-if='group.status' class='ac-oppose' :data-status='group.status'>{{ opposeLabel(group.status) }}</p>
          <p v-if='!group.refs.length' class='ac-empty'>无</p>
          <ul v-else class='ac-refs'>
            <li v-for='(ref, index) in group.refs' :key='`${group.key}-${index}`' class='ac-ref' :data-kind='ref.kind'>
              <div class='ac-ref-head'>
                <span class='ac-kind'>{{ kindLabel(ref.kind) }}</span>
                <span class='ac-label'>{{ ref.label }}</span>
              </div>
              <p v-if='ref.excerpt' class='ac-excerpt'>{{ ref.excerpt }}</p>
              <p v-if='ref.notice' class='ac-ref-notice'>{{ ref.notice }}</p>
              <div class='ac-ref-actions'>
                <button v-if='isFact(ref)' type='button' class='ac-link' :disabled='inlineFor(ref).loading' @click='toggleInline(ref)'>{{ inlineFor(ref).open ? '收起证据行' : '查看证据行' }}</button>
                <button v-else-if='isTool(ref)' type='button' class='ac-link' :disabled='inlineFor(ref).loading' @click='toggleInline(ref)'>{{ inlineFor(ref).open ? '收起工具输出' : '查看工具输出' }}</button>
                <button v-else-if='isVideo(ref)' type='button' class='ac-link' :disabled='inlineFor(ref).loading' @click='openVideoFrame(ref)'>{{ inlineFor(ref).loading ? '正在打开…' : '查看视频帧' }}</button>
                <button v-else-if='isRecord(ref) && recordRoute(ref)' type='button' class='ac-link' @click='openRoute(recordRoute(ref))'>查看原始记录</button>
                <button v-else-if='isRag(ref) && ref.tool_id' type='button' class='ac-link' :disabled='inlineFor(ref).loading' @click='toggleInline(ref)'>{{ inlineFor(ref).open ? '收起检索快照' : '查看检索快照' }}</button>
                <span v-if='isRag(ref)' class='ac-tag'>历史参考 · 非本次观测</span>
              </div>
              <div v-if='inlineFor(ref).open' class='ac-inline'>
                <p v-if='inlineFor(ref).loading' class='ac-note'>加载中…</p>
                <p v-else-if='inlineFor(ref).error' class='ac-error'>{{ inlineFor(ref).error }}</p>
                <pre v-else class='ac-pre'>{{ inlineFor(ref).text }}</pre>
              </div>
            </li>
          </ul>
        </details>
        <section v-if='card.gaps.length' class='ac-group'>
          <h4 class='ac-group-title'>证据缺口<span class='ac-count'>{{ card.gaps.length }}</span></h4>
          <ul class='ac-gaps'>
            <li v-for='(gap, index) in card.gaps' :key='index'>{{ gap }}</li>
          </ul>
        </section>
        <section v-if='hasSuggestion(card)' class='ac-suggestion'>
          <h4 class='ac-group-title'>建议复测（假设，非结论）</h4>
          <p class='ac-hypothesis'><strong>{{ card.suggestion.title }}</strong>：{{ card.suggestion.hypothesis }}</p>
          <p v-if='card.suggestion.supporting_notes' class='ac-note'>支持说明：{{ card.suggestion.supporting_notes }}</p>
          <p v-if='card.suggestion.opposing_notes' class='ac-note'>反向说明：{{ card.suggestion.opposing_notes }}</p>
          <dl class='ac-retest'>
            <dt>地点</dt><dd>{{ card.suggestion.retest.location || '未指定' }}</dd>
            <dt>指标</dt><dd>{{ card.suggestion.retest.indicators.map(key=>INDICATORS[key]||key).join('、') || '未指定' }}</dd>
            <dt>计划</dt><dd>{{ card.suggestion.retest.plan || '未指定' }}</dd>
          </dl>
        </section>
        <div class='ac-card-actions'>
          <button v-if='preview?.cardId !== card.id' type='button' class='ac-primary' :disabled='!canStartPreview() || confirmed.has(card.id)' @click='startPreview(card)'>{{ previewLoadingFor === card.id ? '正在生成草稿…' : '生成复测草稿' }}</button>
          <span v-if='confirmed.has(card.id)' class='ac-badge ac-badge--ok'>已保存到排查板</span>
          <span v-if='preview && preview.cardId !== card.id' class='ac-note'>请先确认或取消当前草稿</span>
        </div>
        <p v-if='cardErrors[card.id]' class='ac-error'>{{ cardErrors[card.id] }}</p>
        <form v-if='preview && preview.cardId === card.id' class='ac-preview' @submit.prevent='submitConfirm'>
          <div class='ac-preview-head'>
            <strong>复测草稿（可编辑，确认前不会写入排查板）</strong>
            <span v-if='preview.notice' class='ac-note'>{{ preview.notice }}</span>
          </div>
          <label class='ac-field'><span>标题</span><input v-model='preview.editor.title' type='text' maxlength='120' :disabled='preview.submitting' /></label>
          <label class='ac-field'><span>复测假设</span><textarea v-model='preview.editor.hypothesis' rows='3' maxlength='2000' :disabled='preview.submitting'></textarea></label>
          <div class='ac-grid'>
            <label class='ac-field'><span>支持性备注</span><textarea v-model='preview.editor.supportingNotes' rows='3' maxlength='4000' :disabled='preview.submitting'></textarea></label>
            <label class='ac-field'><span>反向备注</span><textarea v-model='preview.editor.opposingNotes' rows='3' maxlength='4000' :disabled='preview.submitting'></textarea></label>
          </div>
          <div class='ac-grid'>
            <label class='ac-field'><span>地点</span><input v-model='preview.editor.location' type='text' maxlength='300' :disabled='preview.submitting' /></label>
            <div class='ac-field ac-coords'><span>参考坐标（关联记录，不可编辑）</span><p>{{ coordinateText(preview.base.retest) }}</p></div>
          </div>
          <fieldset class='ac-indicators'><legend>复测指标</legend><label v-for='(label,key) in INDICATORS' :key='key'><input v-model='preview.editor.indicators' type='checkbox' :value='key' :disabled='preview.submitting' />{{ label }}</label></fieldset>
          <label class='ac-field'><span>复测计划</span><textarea v-model='preview.editor.plan' rows='3' maxlength='3000' :disabled='preview.submitting'></textarea></label>
          <p v-if='preview.error' class='ac-error' role='alert'>{{ preview.error }}</p>
          <p class='ac-note'>确认后以“待排查”状态创建任务，复核结论与实际复测结果须后续人工记录。</p>
          <div class='ac-preview-actions'>
            <button type='submit' class='ac-primary' :disabled='preview.submitting || preview.blocked'>{{ preview.submitting ? '正在保存…' : '确认保存到排查板' }}</button>
            <button type='button' class='ac-secondary' :disabled='preview.submitting' @click='cancelPreview'>取消</button>
          </div>
        </form>
      </article>
    </div>
  </section>
</template>

<style scoped>
.ac { min-width:0; margin-top: 12px; border: 1px solid var(--c-border, #d8dee7); border-radius: var(--radius-md, 12px); background: var(--c-surface, #fff); color: var(--c-ink, #1f2733); font-size: 14px; line-height: 1.55; overflow: hidden; }
.ac-toggle { display: flex; align-items: center; gap: 10px; width: 100%; padding: 10px 14px; border: 0; background: var(--c-surface-2, #f5f7fb); color: inherit; font: inherit; text-align: left; cursor: pointer; }
.ac-toggle:hover { background: var(--c-surface-2, #eef2f8); }
.ac-toggle:focus-visible { outline: 2px solid var(--c-accent, #2f6fed); outline-offset: -2px; }
.ac-heading { font-weight: 600; flex: 0 0 auto; }
.ac-meta { flex: 1 1 auto; min-width: 0; color: var(--c-sub, #5b6778); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ac-chevron { flex: 0 0 auto; width: 18px; text-align: center; font-weight: 700; color: var(--c-sub, #5b6778); }
.ac-body { display: flex; flex-direction: column; gap: 12px; padding: 12px 14px 14px; border-top: 1px solid var(--c-border, #d8dee7); }
.ac-alert { margin: 0; padding: 8px 10px; border-radius: 8px; background: var(--c-warning-bg, #332c1e); border: 1px solid var(--c-warning-border, #725a30); color: var(--c-ink, #1f2733); font-size: 13px; }
.ac-alert[data-tone='failed'], .ac-alert[data-tone='invalid'] { background: var(--c-danger-bg, #33232b); border-color: var(--c-danger-border, #794354); }
.ac-alert--ok { background: var(--c-success-bg, #19382f); border-color: var(--c-success-border, #35654a); }
.ac-note { margin: 0; font-size: 12px; color: var(--c-sub, #5b6778); }
.ac-error { margin: 0; font-size: 12px; color: var(--c-danger, #ffbdc7); overflow-wrap: anywhere; }
.ac-card { min-width:0; display: flex; flex-direction: column; gap: 10px; padding: 12px; border: 1px solid var(--c-border, #d8dee7); border-radius: 10px; background: var(--c-surface, #fff); }
.ac-card-head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.ac-title { margin: 0; font-size: 15px; font-weight: 600; overflow-wrap: anywhere; }
.ac-badge { padding: 1px 6px; border-radius: 999px; background: var(--c-surface-2, #eef2f8); color: var(--c-sub, #5b6778); font-size: 11px; white-space: nowrap; }
.ac-badge--ok { background: var(--c-success-bg, #19382f); color: var(--c-success, #9fe2b9); }
.ac-finding { margin: 0; overflow-wrap: anywhere; }
.ac-group { display: flex; flex-direction: column; gap: 6px; }
.ac-group-title { margin: 0; font-size: 13px; font-weight: 600; color: var(--c-sub, #5b6778); }
.ac-count { margin-left: 6px; font-weight: 400; }
.ac-oppose { margin: 0; font-size: 12px; color: var(--c-sub, #5b6778); }
.ac-oppose[data-status='not_provided'] { color: var(--c-warning, #edc37b); }
.ac-empty { margin: 0; font-size: 12px; color: var(--c-sub, #5b6778); }
.ac-refs { display: flex; flex-direction: column; gap: 8px; margin: 0; padding: 0; list-style: none; }
.ac-gaps { display: flex; flex-direction: column; gap: 4px; margin: 0; padding-left: 16px; list-style: disc; font-size: 13px; }
.ac-ref { padding: 8px; border: 1px solid var(--c-border, #d8dee7); border-radius: 8px; background: var(--c-surface-2, #f7f9fc); }
.ac-ref-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px; }
.ac-kind { padding: 0 6px; border-radius: 4px; background: var(--c-surface-2, #e8eef7); font-size: 11px; }
.ac-label { font-size: 13px; font-weight: 600; overflow-wrap: anywhere; }
.ac-excerpt { margin: 4px 0 0; font-size: 12px; white-space: pre-wrap; overflow-wrap: anywhere; max-height: 120px; overflow: auto; }
.ac-ref-notice { margin: 4px 0 0; font-size: 11px; color: var(--c-sub, #5b6778); }
.ac-ref-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 6px; }
.ac-link { padding: 0; border: 0; background: none; color: var(--c-accent, #2f6fed); font: inherit; font-size: 12px; cursor: pointer; text-decoration: underline; }
.ac-link:disabled { color: var(--c-sub, #8a94a6); cursor: progress; }
.ac-tag { font-size: 11px; color: var(--c-sub, #5b6778); }
.ac-inline { margin-top: 6px; padding: 6px 8px; border-radius: 6px; background: var(--c-surface, #fff); border: 1px dashed var(--c-border, #d8dee7); }
.ac-pre { margin: 0; max-height: 220px; overflow: auto; font-family: var(--c-font-mono, ui-monospace, monospace); font-size: 11px; white-space: pre-wrap; overflow-wrap: anywhere; }
.ac-suggestion { display: flex; flex-direction: column; gap: 4px; padding: 8px; border-radius: 8px; background: var(--c-surface-2, #f7f9fc); }
.ac-hypothesis { margin: 0; font-size: 13px; overflow-wrap: anywhere; }
.ac-retest { display: grid; grid-template-columns: 44px 1fr; gap: 2px 8px; margin: 0; font-size: 12px; }
.ac-retest dt { color: var(--c-sub, #5b6778); }
.ac-retest dd { margin: 0; overflow-wrap: anywhere; }
.ac-card-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.ac-primary, .ac-secondary { padding: 6px 12px; border-radius: 8px; font: inherit; font-size: 13px; cursor: pointer; }
.ac-primary { border: 1px solid var(--c-accent, #2f6fed); background: var(--c-accent, #2f6fed); color: var(--c-bg, #fff); }
.ac-primary:disabled, .ac-secondary:disabled { opacity: 0.55; cursor: not-allowed; }
.ac-secondary { border: 1px solid var(--c-border, #d8dee7); background: var(--c-surface, #fff); color: inherit; }
.ac-preview { display: flex; flex-direction: column; gap: 8px; padding: 10px; border: 1px solid var(--c-accent-border, #c7d8fb); border-radius: 10px; background: var(--c-surface-2, #f7f9fc); }
.ac-preview-head { display: flex; flex-direction: column; gap: 2px; font-size: 13px; }
.ac-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(220px, 100%), 1fr)); gap: 8px; }
.ac-field { min-width:0; display: flex; flex-direction: column; gap: 3px; font-size: 12px; color: var(--c-sub, #5b6778); }
.ac-field input, .ac-field textarea { width: 100%; box-sizing: border-box; padding: 6px 8px; border: 1px solid var(--c-border, #d8dee7); border-radius: 6px; background: var(--c-surface, #fff); color: var(--c-ink, #1f2733); font: inherit; font-size: 13px; resize: vertical; }
.ac-field input:focus-visible, .ac-field textarea:focus-visible { outline: 2px solid var(--c-accent, #2f6fed); outline-offset: 0; }
.ac-coords p { margin: 0; padding: 6px 8px; border: 1px dashed var(--c-border, #d8dee7); border-radius: 6px; font-size: 13px; color: var(--c-ink, #1f2733); }
.ac-preview-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.ac-indicators{display:flex;gap:10px;flex-wrap:wrap;border:1px solid var(--c-border);border-radius:8px;margin:0;padding:8px 10px;color:var(--c-sub)}
.ac-indicators label{display:flex;align-items:center;gap:5px}.ac-indicators input{width:auto}.ac-indicators legend{font-size:12px}
@media (max-width: 480px) {
  .ac { font-size: 13px; }
  .ac-toggle { padding: 9px 10px; }
  .ac-meta { display: none; }
  .ac-body { padding: 10px; }
  .ac-retest { grid-template-columns: 1fr; }
}
</style>
