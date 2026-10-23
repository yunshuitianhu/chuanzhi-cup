import test from 'node:test'
import assert from 'node:assert/strict'

const memory=new Map()
globalThis.localStorage={getItem:k=>memory.get(k)||null,setItem:(k,v)=>memory.set(k,v),removeItem:k=>memory.delete(k)}
const {api,ApiError,clearSession,sseHeaders,errorMessage}=await import('../src/api/client.js')
const response=(body,status=200)=>new Response(JSON.stringify(body),{status,headers:{'X-Session-Id':'core-test','Content-Type':'application/json'}})

test('首个JSON响应回收会话，后续上传与SSE复用，FormData保留浏览器boundary',async()=>{
  clearSession()
  const requests=[]
  globalThis.fetch=async(path,options)=>{requests.push({path,...options});return response({rows:0})}
  await api.status()
  await api.uploadData(new File(['zero'],'synthetic.csv'))
  assert.equal(requests[0].headers['X-Session-Id'],undefined)
  assert.equal(requests[1].headers['X-Session-Id'],'core-test')
  assert.equal(requests[1].headers['Content-Type'],undefined)
  assert.ok(requests[1].body instanceof FormData)
  assert.equal(sseHeaders()['X-Session-Id'],'core-test')
  clearSession()
  assert.equal(sseHeaders()['X-Session-Id'],undefined)
})

test('项目导出返回Blob，失败响应仍解析为ApiError',async()=>{
  globalThis.fetch=async()=>new Response('package',{headers:{'Content-Type':'application/zip'}})
  const blob=await api.exportProject('id',null)
  assert.ok(blob instanceof Blob)
  assert.equal(await blob.text(),'package')
  globalThis.fetch=async()=>response({detail:'项目不存在'},404)
  await assert.rejects(api.exportProject('missing',null),e=>e instanceof ApiError && e.status===404 && e.message==='项目不存在')
})

test('422参数位置可读，畸形错误项不会掩盖原HTTP状态',async()=>{
  globalThis.fetch=async()=>response({detail:[{loc:['body','duration_h'],msg:'范围错误'},null]},422)
  await assert.rejects(api.riverHydro({duration_h:0}),e=>e instanceof ApiError && e.status===422 && /duration_h：范围错误/.test(e.message))
  assert.equal(errorMessage(null),'null')
  assert.equal(errorMessage({message:'保留原错误'}),'保留原错误')
})

test('过期证据不重试，临时会话占用最多重试两次',async()=>{
  let count=0
  globalThis.fetch=async()=>{count++;return response({detail:'报告已过期'},409)}
  await assert.rejects(api.reportEvidence('old'),/报告已过期/)
  assert.equal(count,1)
  count=0
  globalThis.fetch=async()=>{count++;return response({detail:'当前会话正在处理任务，请完成后再重试'},409)}
  await assert.rejects(api.reportEvidence('busy'),/正在处理任务/)
  assert.equal(count,3)
})
