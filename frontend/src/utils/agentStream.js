/** SSE按UTF-8和完整帧解析；缺少终止事件时不把EOF当作成功。 */
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
        let payload
        try { payload=JSON.parse(data) } catch { throw new Error('分析事件不完整，请核对任务状态') }
        if(!payload || typeof payload!=='object' || Array.isArray(payload))throw new Error('分析事件格式无效')
        if(event==='done' && !['succeeded','partial','failed','cancelled'].includes(payload.status))throw new Error('分析缺少有效结束状态')
        if(event==='done'||event==='error')terminal=true
        await onEvent(event,payload)
      }
      if(done)break
    }
    if(!terminal)throw new Error('分析连接已中断，尚未收到完成状态，请核对服务端任务')
  } catch(error) {
    await reader.cancel().catch(()=>{})
    throw error
  } finally { reader.releaseLock() }
}
