/** @typedef {import('../types/contracts').JsonObject} JsonObject */
/** @typedef {{method?:string, body?:unknown, isForm?:boolean, binary?:boolean, signal?:AbortSignal}} RequestOptions */
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
  /** @type {Record<string,string>} */
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
/** @param {Response} res */
export function adoptSessionFrom(res) {
  const sid = res.headers.get('X-Session-Id')
  if (sid && sid !== sessionId) {
    sessionId = sid
    localStorage.setItem('wq_session_id', sid)
  }
}

class ApiError extends Error {
  /** @param {string} message @param {number} status @param {unknown} payload */
  constructor(message, status, payload) {
    super(message)
    this.status = status
    this.payload = payload
  }
}

/** @param {string} path @param {RequestOptions} options @returns {Promise<unknown>} */
async function readResponse(path, { method = 'GET', body, isForm = false, binary = false, signal } = {}) {
  /** @type {Record<string,string>} */
  const headers = {}
  if (sessionId) headers['X-Session-Id'] = sessionId
  if (!isForm && body !== undefined) headers['Content-Type'] = 'application/json'

  const res = await fetch(BASE + path, {
    method,
    signal,
    headers,
    body: isForm ? /** @type {FormData} */ (body) : (body !== undefined ? JSON.stringify(body) : undefined),
  })

  // 后端在每次响应里回传当前会话 id
  const sid = res.headers.get('X-Session-Id')
  if (sid && sid !== sessionId) {
    sessionId = sid
    localStorage.setItem('wq_session_id', sid)
  }

  if (binary && res.ok) return res.blob()

  /** @type {unknown} */
  let payload = null
  const text = await res.text()
  if (text) {
    try { payload = JSON.parse(text) } catch { payload = text }
  }

  if (!res.ok) {
    // FastAPI 的错误体是 {detail: ...}，尽量把可读信息透出去
    const detail = isRecord(payload) ? payload.detail : undefined
    const msg = Array.isArray(detail)
      ? detail.map((item) => {
        const x=isRecord(item)?item:{}
        const loc=Array.isArray(x.loc)?x.loc:[]
        return `${loc.filter((v) => v !== 'body').join('.')}：${x.msg || '参数无效'}`
      }).join('；')
      : detail || (typeof payload === 'string' ? payload : `HTTP ${res.status}`)
    throw new ApiError(String(msg), res.status, payload)
  }
  return payload
}

/** JSON仅在此边界断言契约；静态类型不替代服务端验证，SSE另有运行时检查。 */
/** @template [T=JsonObject] @param {string} path @param {RequestOptions} [options] @returns {Promise<T>} */
async function request(path, options) {
  return /** @type {T} */ (await readResponse(path, options))
}
/** @param {string} path @param {RequestOptions} options @returns {Promise<Blob>} */
async function requestBlob(path, options) {
  return /** @type {Blob} */ (await readResponse(path, {...options,binary:true}))
}
/** @param {unknown} value @returns {value is Record<string,unknown>} */
function isRecord(value) { return value !== null && typeof value === 'object' && !Array.isArray(value) }
/** @param {unknown} error @returns {string} */
export function errorMessage(error) {
  if (error instanceof Error) return error.message
  if (isRecord(error) && typeof error.message === 'string') return error.message
  return String(error)
}

// 只读快照与播放器可能同时取得会话锁。仅对短暂占用有限重试；过期证据不重试。
/** @template [T=JsonObject] @param {string} path @param {RequestOptions} [options] @returns {Promise<T>} */
async function consistentRead(path, options={}) {
  for(let attempt=0;;attempt++) {
    options.signal?.throwIfAborted()
    try {return /** @type {T} */ (await request(path,options))} catch(error) {
      if(!(error instanceof ApiError) || error.status!==409 || !isRecord(error.payload) || error.payload.detail!=='当前会话正在处理任务，请完成后再重试'
        || attempt>=2 || options.signal?.aborted)throw error
      await new Promise(resolve=>setTimeout(resolve,attempt===0?150:450))
    }
  }
}

export const api = {
  /** @param {Record<string,string>} [params] */
  dataQuality: (params = {}) => request('/analysis/quality?' + new URLSearchParams(params)),
  /** @param {import('../types/contracts').QualityConfig} body */
  updateQualityConfig: (body) => request('/analysis/quality-config', { method:'POST', body }),
  /** @returns {Promise<import('../types/contracts').ProjectState>} */
  projects: () => request('/projects'),
  /** @param {import('../types/contracts').CreateProjectRequest} payload @returns {Promise<import('../types/contracts').ProjectState>} */
  createProject: (payload) => request('/projects', { method:'POST', body:payload }),
  /** @param {import('../types/contracts').SaveProjectRequest} payload @returns {Promise<import('../types/contracts').ProjectState>} */
  saveProject: (payload) => request('/projects/save', { method:'POST', body:payload }),
  /** @param {string} id @param {Partial<import('../types/contracts').RiverForm>|null} riverForm @returns {Promise<import('../types/contracts').ProjectState>} */
  openProject: (id, riverForm) => request(`/projects/${id}/open`, { method:'POST', body:{river_form:riverForm} }),
  /** @param {string} id @param {Partial<import('../types/contracts').RiverForm>|null} riverForm */
  exportProject: (id, riverForm) => requestBlob(`/projects/${id}/export`, { method:'POST', body:{river_form:riverForm}, binary:true }),
  /** @param {File} file @param {Partial<import('../types/contracts').RiverForm>|null} riverForm @returns {Promise<import('../types/contracts').ProjectState>} */
  importProject: (file, riverForm) => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('river_form', JSON.stringify(riverForm))
    return request('/projects/import', { method:'POST', body:fd, isForm:true })
  },
  /** @param {AbortSignal} [signal] */
  health: (signal) => request('/health', { signal }),
  /** @returns {Promise<import('../types/contracts').AgentRun>} */
  agentRun: () => request('/agent/run', { signal:AbortSignal.timeout(10000) }),
  /** @param {string} id @returns {Promise<import('../types/contracts').ActiveRun>} */
  stopAgentRun: (id) => request(`/agent/runs/${encodeURIComponent(id)}/stop`, { method:'POST', signal:AbortSignal.timeout(10000) }),
  /** @returns {Promise<import('../types/contracts').ChatHistory>} */
  chatHistory: () => request('/agent/history'),
  /** @param {string} id */
  reportEvidence: (id) => consistentRead(`/agent/evidence/${encodeURIComponent(id)}`),
  /** @param {string} id @param {string} fact @param {number} [offset] */
  evidenceRows: (id, fact, offset=0) => consistentRead(`/agent/evidence/${encodeURIComponent(id)}/facts/${encodeURIComponent(fact)}/rows?offset=${offset}&limit=20`),
  /** @param {string} id @param {number} position */
  evidenceRecord: (id, position) => consistentRead(`/agent/evidence/${encodeURIComponent(id)}/records/${position}`),
  /** @param {string} id @param {string} tool */
  evidenceTool: (id, tool) => consistentRead(`/agent/evidence/${encodeURIComponent(id)}/tools/${encodeURIComponent(tool)}`),
  /** @returns {Promise<{scope_id:string,revision:number,items:import('../types/contracts').JsonObject[]}>} */
  investigations: () => consistentRead('/investigations'),
  /** @param {JsonObject} body @returns {Promise<import('../types/contracts').InvestigationResult>} */
  createInvestigation: (body) => request('/investigations',{method:'POST',body}),
  /** @param {string} id @param {JsonObject} body @returns {Promise<import('../types/contracts').InvestigationResult>} */
  updateInvestigation: (id,body) => request(`/investigations/${encodeURIComponent(id)}`,{method:'PUT',body}),
  /** @returns {Promise<import('../types/contracts').Config>} */
  config: () => request('/config'),
  /** @param {import('../types/contracts').WaterClass} val @returns {Promise<{ok:boolean,error?:string,analysis_reset?:boolean,target_water_class?:import('../types/contracts').WaterClass}>} */
  setTargetClass: (val) => request('/config/target-class', { method: 'POST', body: { target_water_class: val } }),

  /** @returns {Promise<import('../types/contracts').DataStatus>} */
  status: () => request('/data/status'),
  /** @param {number} [limit] @returns {Promise<import('../types/contracts').Preview>} */
  preview: (limit = 50) => request(`/data/preview?limit=${limit}`),
  /** @param {File} file */
  uploadData: (file) => {
    const fd = new FormData()
    fd.append('file', file)
    return request('/data/upload', { method: 'POST', body: fd, isForm: true })
  },
  /** @param {File} file @param {number} [interval] */
  uploadVideo: (file, interval = 5) => {
    const fd = new FormData()
    fd.append('file', file)
    return request(`/data/video?interval=${interval}`, { method: 'POST', body: fd, isForm: true })
  },

  summary: () => request('/analysis/summary'),
  /** @returns {Promise<import('../types/contracts').AnalysisResults>} */
  analysisResults: () => request('/analysis/results'),
  /** @returns {Promise<import('../types/contracts').BlackSpots>} */
  blackSpots: () => request('/analysis/black-spots', { method: 'POST' }),
  /** @param {import('../types/contracts').CrossValidationRequest} [payload] */
  crossValidate: (payload) => request('/analysis/cross-validate', { method: 'POST', body: payload || {} }),
  /** @returns {Promise<import('../types/contracts').VideoQualityResponse>} */
  videoQuality: (offset = 0) => request(`/video/quality?offset=${offset}&limit=20`),
  /** @param {number} id @param {string} revision @returns {Promise<import('../types/contracts').VideoFrameResponse>} */
  videoFrame: (id, revision) => request(`/video/frames/${id}?expected_revision=${encodeURIComponent(revision)}`),
  /** @param {import('../types/contracts').VideoSettingsChange} payload */
  saveVideoSettings: (payload) => request('/video/settings', { method:'PUT', body:payload }),
  /** @param {number} id @param {import('../types/contracts').JsonObject} payload */
  saveVideoFrame: (id, payload) => request(`/video/frames/${id}`, { method:'PUT', body:payload }),
  comprehensive: () => request('/analysis/comprehensive'),

  timeSeries: () => request('/charts/time-series'),
  traceMap: () => request('/charts/trace-map'),
  /** @param {Record<string,string>} params @param {AbortSignal} [signal] */
  amapStaticMap: (params, signal) => requestBlob(`/maps/amap-static?${new URLSearchParams(params)}`, { binary:true, signal }),
  crossValidationChart: () => request('/charts/cross-validation'),

  // ---- 国控站点 ----
  provinces: () => request('/stations/provinces'),
  /** @param {{province?:string,keyword?:string,live?:boolean}} [params] */
  queryStations: ({ province = '', keyword = '', live = true } = {}) =>
    request(`/stations?province=${encodeURIComponent(province)}&keyword=${encodeURIComponent(keyword)}&live=${live}`),

  // ---- 多渠道溯源 ----
  /** @param {JsonObject} [payload] */
  multiSource: (payload) => request('/trace/multi-source', { method: 'POST', body: payload || {} }),
  /** @param {number} lat @param {number} lon */
  reverseGeocode: (lat, lon) => request(`/trace/reverse-geocode?lat=${lat}&lon=${lon}`),

  // ---- RAG ----
  ragStatus: () => request('/rag/status'),
  /** @param {string} query @param {number} [topK] */
  ragSearch: (query, topK = 5) => request('/rag/search', { method: 'POST', body: { query, top_k: topK } }),

  // ---- 河道模型 ----
  /** @param {string} timezone @param {AbortSignal} [signal] */
  cruiseAudit: (timezone, signal) => request(`/river/alignment/audit?timezone=${encodeURIComponent(timezone)}`, {signal}),
  /** @param {AbortSignal} [signal] */
  cruiseAlignment: (signal) => request('/river/alignment', {signal}),
  /** @param {Record<string,string>} params @param {AbortSignal} [signal] */
  cruiseReference: (params, signal) => request('/river/alignment/reference?'+new URLSearchParams(params), {signal}),
  /** @param {JsonObject} body @param {AbortSignal} [signal] */
  setCruiseAlignment: (body, signal) => request('/river/alignment', {method:'POST',body,signal}),
  riverParams: () => request('/river/params'),
  /** @param {import('../types/contracts').RiverInitRequest} [payload] */
  riverInit: (payload) => request('/river/init', { method: 'POST', body: payload || {} }),
  /** @param {import('../types/contracts').HydroRequest} [payload] */
  riverHydro: (payload) => request('/river/hydro', { method: 'POST', body: payload || {} }),
  /** @param {import('../types/contracts').PollutionRequest} [payload] */
  riverPollution: (payload) => request('/river/pollution', { method: 'POST', body: payload || {} }),
  /** @param {number} timeH @param {'ammonia'|'cbod'|'do'} constituent */
  riverProfile: (timeH, constituent) => request(`/river/profile?time_h=${timeH}&constituent=${constituent}`),
  /** @param {'ammonia'|'cbod'|'do'} [constituent] @param {AbortSignal} [signal] */
  riverPlayback: (constituent = 'ammonia', signal) => consistentRead(`/river/playback?constituent=${encodeURIComponent(constituent)}`, {signal}),
}

export { ApiError }
