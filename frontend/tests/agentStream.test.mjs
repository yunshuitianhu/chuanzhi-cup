import test from 'node:test'
import assert from 'node:assert/strict'
import {consumeAgentStream,decodeAgentEvent} from '../src/utils/agentStream.js'

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
  const stream=new ReadableStream({start(controller){controller.enqueue(new TextEncoder().encode('event: delta\ndata: {"run_id":"old","text":"旧片段"}\n\n'))},cancel(){cancelled=true}})
  await assert.rejects(consumeAgentStream(stream,()=>{throw new Error('过期任务')}),/过期任务/)
  assert.equal(cancelled,true)
})

test('字段错型不能以正常事件交给消费者，零轮次与空增量合法',()=>{
  for (const [event,payload] of [['delta',{text:123}],['turn',{turn:'1'}],['tool',{name:[] }],
    ['tool_done',{name:'x',ok:'false',status:'failed'}],['error',{message:{detail:'失败'}}],
    ['done',{status:'running'}],['delta',{text:'x',run_id:3}],['run',{progress:{busy:'false'}}],
    ['tool_done',{name:'x',ok:true,status:'running'}],['tool_done',{name:'x',ok:true,status:'failed'}]]) {
    assert.throws(()=>decodeAgentEvent(event,payload))
  }
  assert.deepEqual(decodeAgentEvent('turn',{turn:0}),['turn',{turn:0}])
  assert.deepEqual(decodeAgentEvent('delta',{text:''}),['delta',{text:''}])
})

test('未知扩展事件可忽略，但不能代替明确终态',async()=>{
  const events=[]
  await consumeAgentStream(body('event: future\ndata: {}\n\nevent: done\ndata: {"status":"partial"}\n\n'),(...args)=>events.push(args))
  assert.equal(events.length,1)
  assert.equal(events[0][1].status,'partial')
  await assert.rejects(consumeAgentStream(body('event: future\ndata: {}\n\n'),()=>{}),/连接已中断/)
})


test('图片状态事件单独解码，非法状态拒绝，未知事件仍不能充当终态',async()=>{
  assert.deepEqual(decodeAgentEvent('vision',{status:'sent',images:[{sha256:'a'.repeat(64)}],errors:[]}),
    ['vision',{status:'sent',images:[{sha256:'a'.repeat(64)}],errors:[]}])
  assert.deepEqual(decodeAgentEvent('vision',{status:'failed',errors:['图片无法解码']}),
    ['vision',{status:'failed',errors:['图片无法解码']}])
  assert.throws(()=>decodeAgentEvent('vision',{status:'unknown'}))
  assert.throws(()=>decodeAgentEvent('vision',{status:'sent',images:'bad'}))
})
