import test from 'node:test'
import assert from 'node:assert/strict'
import {consumeAgentStream} from '../src/utils/agentStream.js'

function body(text,size=3) {
  const bytes=new TextEncoder().encode(text)
  return new ReadableStream({start(controller){for(let n=0;n<bytes.length;n+=size)controller.enqueue(bytes.slice(n,n+size));controller.close()}})
}
test('UTF8拆片、CRLF与心跳不破坏中文事件或终止状态',async()=>{
  const events=[]
  await consumeAgentStream(body(': heartbeat\r\n\r\nevent: delta\r\ndata: {"text":"水质💧"}\r\n\r\nevent: done\r\ndata: {"status":"succeeded"}\r\n\r\n'),(event,payload)=>events.push([event,payload]))
  assert.equal(events[0][1].text,'水质💧');assert.equal(events[1][1].status,'succeeded')
})
test('没有终止帧的EOF不能视为成功',async()=>{
  await assert.rejects(consumeAgentStream(body('event: delta\ndata: {"text":"半段"}\n\n'),()=>{}),/连接已中断/)
})
test('非法JSON与缺失结束状态会拒绝，而不是静默跳过',async()=>{
  await assert.rejects(consumeAgentStream(body('event: delta\ndata: {broken}\n\n'),()=>{}),/事件不完整/)
  await assert.rejects(consumeAgentStream(body('event: done\ndata: {}\n\n'),()=>{}),/结束状态/)
})
test('停止与错误是明确终止状态，均不等于成功',async()=>{
  const events=[]
  await consumeAgentStream(body('event: done\ndata: {"status":"cancelled"}\n\n'),(event,payload)=>events.push(payload.status))
  await consumeAgentStream(body('event: error\ndata: {"message":"失败"}\n\n'),(event,payload)=>events.push(payload.message))
  assert.deepEqual(events,['cancelled','失败'])
})
test('回调检测到其它任务时取消读取',async()=>{
  let cancelled=false
  const stream=new ReadableStream({start(controller){controller.enqueue(new TextEncoder().encode('event: delta\ndata: {"run_id":"old"}\n\n'))},cancel(){cancelled=true}})
  await assert.rejects(consumeAgentStream(stream,()=>{throw new Error('过期任务')}),/过期任务/)
  assert.equal(cancelled,true)
})
