/** @typedef {import('../types/contracts').AgentEventArgs} AgentEventArgs */
const TERMINAL = ['succeeded','partial','failed','cancelled']
const TOOL_STATUS = ['succeeded','failed','cancelled','running','skipped']
/** @param {unknown} value @returns {value is Record<string,unknown>} */
function record(value) { return value !== null && typeof value === 'object' && !Array.isArray(value) }
/** @param {unknown} value */
function finite(value) { return typeof value === 'number' && Number.isFinite(value) }
/** @param {unknown} value */
function activeRun(value) {
  return record(value) && typeof value.run_id === 'string' && Number.isInteger(value.analysis_revision)
    && typeof value.busy === 'boolean' && typeof value.auto === 'boolean'
    && typeof value.stop_requested === 'boolean'
    && typeof value.status === 'string' && ['running','stopping',...TERMINAL].includes(value.status)
    && ['stage','started_at','notice'].every(k => typeof value[k] === 'string')
    && ['turn','tool_count','elapsed_s'].every(k => finite(value[k]))
    && Array.isArray(value.steps) && value.steps.every(s => record(s) && typeof s.id === 'string'
      && typeof s.name === 'string' && typeof s.status === 'string'
      && TOOL_STATUS.includes(s.status) && finite(s.elapsed_s))
}
/** 外部流必须先校验再交给类型化消费者；未知扩展事件可忽略，不能充当结束事件。
 * @param {string} event @param {unknown} value @returns {AgentEventArgs|null}
 */
export function decodeAgentEvent(event, value) {
  if (!record(value)) throw new Error('分析事件格式无效')
  if (value.run_id !== undefined && typeof value.run_id !== 'string') throw new Error('分析任务标识无效')
  if (value.progress !== undefined && !activeRun(value.progress)) throw new Error('分析任务进度格式无效')
  let valid = true
  switch (event) {
    case 'run': break
    case 'turn': valid = Number.isInteger(value.turn) && Number(value.turn) >= 0; break
    case 'delta': valid = typeof value.text === 'string'; break
    case 'tool': valid = typeof value.name === 'string'
      && (value.args === undefined || (Array.isArray(value.args) && value.args.every(a => typeof a === 'string'))); break
    case 'tool_done': valid = typeof value.name === 'string' && typeof value.ok === 'boolean'
      && typeof value.status === 'string' && ['succeeded','failed','skipped'].includes(value.status)
      && value.ok === (value.status !== 'failed'); break
    case 'vision':
      valid = typeof value.status === 'string' && ['sent','failed'].includes(value.status)
        && (value.images === undefined || (Array.isArray(value.images)
          && value.images.every(image => record(image) && (image.sha256 === undefined || typeof image.sha256 === 'string'))))
        && (value.errors === undefined || (Array.isArray(value.errors) && value.errors.every(text => typeof text === 'string')))
        && (value.error === undefined || typeof value.error === 'string'); break
    case 'done':
      if (typeof value.status !== 'string' || !TERMINAL.includes(value.status)) throw new Error('分析缺少有效结束状态')
      break
    case 'error': valid = typeof value.message === 'string'
      && (value.status === undefined || (typeof value.status === 'string' && TERMINAL.includes(value.status))); break
    default: return null
  }
  if (!valid) throw new Error('分析事件字段无效：'+event)
  if (value.step_id !== undefined && typeof value.step_id !== 'string') throw new Error('分析工具标识无效')
  for (const key of ['turns','tool_errors','chars']) {
    if (value[key] !== undefined && !finite(value[key])) throw new Error('分析事件数值无效：'+key)
  }
  if (value.message !== undefined && typeof value.message !== 'string') throw new Error('分析提示格式无效')
  // 上述分支检查完整的联合字段；JSON边界的断言集中在这里。
  return /** @type {AgentEventArgs} */ ([event,value])
}

/** SSE按UTF-8和完整帧解析；缺少终止事件时不把EOF当作成功。
 * @param {ReadableStream<Uint8Array>} body
 * @param {(...args:AgentEventArgs) => unknown|Promise<unknown>} onEvent
 */
export async function consumeAgentStream(body, onEvent) {
  const reader=body.getReader(), decoder=new TextDecoder()
  let buffer='', terminal=false
  function frames() {
    const chunks=buffer.split(/\r?\n\r?\n/)
    buffer=chunks.pop() || ''
    return chunks
  }
  try {
    for (;;) {
      const {done,value}=await reader.read()
      buffer+=done ? decoder.decode() : decoder.decode(value,{stream:true})
      if(buffer.length>1024*1024)throw new Error('分析事件过大，连接已停止，请核对任务状态')
      for(const frame of frames()) {
        const lines=frame.split(/\r?\n/)
        const data=lines.filter(line=>line.startsWith('data:')).map(line=>line.slice(5).trimStart()).join('\n')
        if(!data)continue
        const event=lines.find(line=>line.startsWith('event:'))?.slice(6).trim() || 'message'
        /** @type {unknown} */
        let payload
        try { payload=JSON.parse(data) } catch { throw new Error('分析事件不完整，请核对任务状态') }
        const decoded=decodeAgentEvent(event,payload)
        if(!decoded)continue
        if(event==='done'||event==='error')terminal=true
        await onEvent(...decoded)
      }
      if(done)break
    }
    if(!terminal)throw new Error('分析连接已中断，尚未收到完成状态，请核对服务端任务')
  } catch(error) {
    await reader.cancel().catch(()=>{})
    throw error
  } finally { reader.releaseLock() }
}
