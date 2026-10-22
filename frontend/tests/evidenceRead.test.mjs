import test from 'node:test'
import assert from 'node:assert/strict'
globalThis.localStorage={getItem:()=>null,setItem:()=>{},removeItem:()=>{}}
const {api}=await import('../src/api/client.js')
const busy=()=>new Response(JSON.stringify({detail:'当前会话正在处理任务，请完成后再重试'}),{status:409})

test('证据只读请求短暂占用后重试成功',async()=>{
  let count=0
  globalThis.fetch=async()=>++count===1?busy():new Response('{"position":2}',{status:200})
  assert.equal((await api.evidenceRecord('controlled',2)).position,2)
  assert.equal(count,2)
})
test('过期证据直接拒绝，不因409而重试',async()=>{
  let count=0
  globalThis.fetch=async()=>{count++;return new Response('{"detail":"历史证据已过期"}',{status:409})}
  await assert.rejects(api.evidenceRecord('old',2),/已过期/)
  assert.equal(count,1)
})
test('播放器等待短暂读锁后显示真实模型缺失原因',async()=>{
  let count=0
  globalThis.fetch=async()=>++count===1?busy():new Response('{"detail":"请先运行模型"}',{status:400})
  await assert.rejects(api.riverPlayback(),/请先运行模型/)
  assert.equal(count,2)
})
test('持续占用有重试上限，写操作不重试',async()=>{
  let count=0
  globalThis.fetch=async()=>{count++;return busy()}
  await assert.rejects(api.reportEvidence('controlled'),/正在处理/)
  assert.equal(count,3)
  count=0
  await assert.rejects(api.setTargetClass('III'),/正在处理/)
  assert.equal(count,1)
})
test('取消播放器读取后不再发起重试',async()=>{
  const controller=new AbortController();let count=0
  globalThis.fetch=async()=>{count++;controller.abort();return busy()}
  await assert.rejects(api.riverPlayback('ammonia',controller.signal))
  assert.equal(count,1)
})
