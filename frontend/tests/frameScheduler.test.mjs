import test from 'node:test'
import assert from 'node:assert/strict'
import { frameScheduler } from '../src/utils/frameScheduler.js'

test('同一帧的重复重绘合并；下一帧可继续', () => {
  let callback, requests=0, draws=0
  const scheduler=frameScheduler(()=>draws++, fn=>{callback=fn;return ++requests},()=>{})
  for(let i=0;i<10;i++)scheduler.schedule()
  assert.equal(requests,1);assert.equal(draws,0)
  callback();assert.equal(draws,1)
  scheduler.schedule();assert.equal(requests,2);callback();assert.equal(draws,2)
})

test('卸载取消待执行任务，陈旧回调和后续调度均不绘制', () => {
  let callback, cancelled=[],draws=0,requests=0
  const scheduler=frameScheduler(()=>draws++,fn=>{callback=fn;requests++;return 42},id=>cancelled.push(id))
  scheduler.schedule();scheduler.dispose();callback();scheduler.schedule();scheduler.dispose()
  assert.deepEqual(cancelled,[42]);assert.equal(requests,1);assert.equal(draws,0)
})
