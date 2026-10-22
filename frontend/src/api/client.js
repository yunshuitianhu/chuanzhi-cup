/**
 * 后端 API 客户端。
 *
 * 会话：后端按 X-Session-Id 区分会话（对应原 Streamlit 的 session_state）。
 * 首次请求不带该头，后端会新建并返回 session_id，之后每个请求都带上。
 */
const BASE = '/api'

let sessionId = localStorage.getItem('wq_session_id') || ''

export function getSessionId() {
  return sessionId
}

export function clearSession() {
  sessionId = ''
  localStorage.removeItem('wq_session_id')
}

/**
 * SSE 请求（Agent 对话）用的请求头。
 *
 * ⚠️ **必须带上 `X-Session-Id`**。SSE 无法用下面的 `request()` 包装器
 * （它要读流式 body），只能自己发 fetch —— 但会话头不能省。
 *
 * 少了它后端会**新建一个空会话**：Agent 的工具全部报"数据未加载"，
 * 而侧栏/顶栏（走 `request()`，带了头）却正常显示"1805 条"。
 * **界面自相矛盾，且接口全部返回 200，极难发现。** 这个坑实际发生过。
 */
export function sseHeaders() {
  const headers = { 'Content-Type': 'application/json' }
  if (sessionId) headers['X-Session-Id'] = sessionId
  return headers
}

/**
 * 从 SSE 响应里回收后端回传的会话 id。
 *
 * 首次请求没带 id 时后端会新建，并把新 id 放在响应头里 —— 必须接住，
 * 否则后续请求仍会各自新建会话。`request()` 里也有同样一段逻辑。
 */
export function adoptSessionFrom(res) {
  const sid = res.headers.get('X-Session-Id')
  if (sid && sid !== sessionId) {
    sessionId = sid
    localStorage.setItem('wq_session_id', sid)
  }
}

class ApiError extends Error {
  constructor(message, status, payload) {
    super(message)
    this.status = status
    this.payload = payload
  }
}

async function request(path, { method = 'GET', body, isForm = false, binary = false, signal } = {}) {
  const headers = {}
  if (sessionId) headers['X-Session-Id'] = sessionId
  if (!isForm && body !== undefined) headers['Content-Type'] = 'application/json'

  const res = await fetch(BASE + path, {
    method,
    signal,
    headers,
    body: isForm ? body : (body !== undefined ? JSON.stringify(body) : undefined),
  })

  // 后端在每次响应里回传当前会话 id
  const sid = res.headers.get('X-Session-Id')
  if (sid && sid !== sessionId) {
    sessionId = sid
    localStorage.setItem('wq_session_id', sid)
  }

  if (binary && res.ok) return res.blob()

  let payload = null
  const text = await res.text()
  if (text) {
    try { payload = JSON.parse(text) } catch { payload = text }
  }

  if (!res.ok) {
    // FastAPI 的错误体是 {detail: ...}，尽量把可读信息透出去
    const detail = payload?.detail
    const msg = Array.isArray(detail)
      ? detail.map((x) => `${(x.loc || []).filter((v) => v !== 'body').join('.')}：${x.msg}`).join('；')
      : detail || (typeof payload === 'string' ? payload : `HTTP ${res.status}`)
    throw new ApiError(String(msg), res.status, payload)
  }
  return payload
}

export const api = {
  dataQuality: (params = {}) => request('/analysis/quality?' + new URLSearchParams(params)),
  updateQualityConfig: (body) => request('/analysis/quality-config', { method:'POST', body }),
  projects: () => request('/projects'),
  createProject: (payload) => request('/projects', { method:'POST', body:payload }),
  saveProject: (payload) => request('/projects/save', { method:'POST', body:payload }),
  openProject: (id, riverForm) => request(`/projects/${id}/open`, { method:'POST', body:{river_form:riverForm} }),
  exportProject: (id, riverForm) => request(`/projects/${id}/export`, { method:'POST', body:{river_form:riverForm}, binary:true }),
  importProject: (file, riverForm) => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('river_form', JSON.stringify(riverForm))
    return request('/projects/import', { method:'POST', body:fd, isForm:true })
  },
  health: (signal) => request('/health', { signal }),
  agentRun: () => request('/agent/run', { signal:AbortSignal.timeout(10000) }),
  stopAgentRun: (id) => request(`/agent/runs/${encodeURIComponent(id)}/stop`, { method:'POST', signal:AbortSignal.timeout(10000) }),
  chatHistory: () => request('/agent/history'),
  config: () => request('/config'),
  setTargetClass: (val) => request('/config/target-class', { method: 'POST', body: { target_water_class: val } }),

  status: () => request('/data/status'),
  preview: (limit = 50) => request(`/data/preview?limit=${limit}`),
  uploadData: (file) => {
    const fd = new FormData()
    fd.append('file', file)
    return request('/data/upload', { method: 'POST', body: fd, isForm: true })
  },
  uploadVideo: (file, interval = 5) => {
    const fd = new FormData()
    fd.append('file', file)
    return request(`/data/video?interval=${interval}`, { method: 'POST', body: fd, isForm: true })
  },

  summary: () => request('/analysis/summary'),
  analysisResults: () => request('/analysis/results'),
  blackSpots: () => request('/analysis/black-spots', { method: 'POST' }),
  crossValidate: (payload) => request('/analysis/cross-validate', { method: 'POST', body: payload || {} }),
  comprehensive: () => request('/analysis/comprehensive'),

  timeSeries: () => request('/charts/time-series'),
  traceMap: () => request('/charts/trace-map'),
  amapStaticMap: (params, signal) => request(`/maps/amap-static?${new URLSearchParams(params)}`, { binary:true, signal }),
  crossValidationChart: () => request('/charts/cross-validation'),

  // ---- 国控站点 ----
  provinces: () => request('/stations/provinces'),
  queryStations: ({ province = '', keyword = '', live = true } = {}) =>
    request(`/stations?province=${encodeURIComponent(province)}&keyword=${encodeURIComponent(keyword)}&live=${live}`),

  // ---- 多渠道溯源 ----
  multiSource: (payload) => request('/trace/multi-source', { method: 'POST', body: payload || {} }),
  reverseGeocode: (lat, lon) => request(`/trace/reverse-geocode?lat=${lat}&lon=${lon}`),

  // ---- RAG ----
  ragStatus: () => request('/rag/status'),
  ragSearch: (query, topK = 5) => request('/rag/search', { method: 'POST', body: { query, top_k: topK } }),

  // ---- 河道模型 ----
  cruiseAudit: (timezone, signal) => request(`/river/alignment/audit?timezone=${encodeURIComponent(timezone)}`, {signal}),
  cruiseAlignment: (signal) => request('/river/alignment', {signal}),
  cruiseReference: (params, signal) => request('/river/alignment/reference?'+new URLSearchParams(params), {signal}),
  setCruiseAlignment: (body, signal) => request('/river/alignment', {method:'POST',body,signal}),
  riverParams: () => request('/river/params'),
  riverInit: (payload) => request('/river/init', { method: 'POST', body: payload || {} }),
  riverHydro: (payload) => request('/river/hydro', { method: 'POST', body: payload || {} }),
  riverPollution: (payload) => request('/river/pollution', { method: 'POST', body: payload || {} }),
  riverProfile: (timeH, constituent) => request(`/river/profile?time_h=${timeH}&constituent=${constituent}`),
  riverPlayback: (constituent = 'ammonia', signal) => request(`/river/playback?constituent=${encodeURIComponent(constituent)}`, {signal}),
}

export { ApiError }
