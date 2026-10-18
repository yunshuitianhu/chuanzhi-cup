import test from 'node:test'
import assert from 'node:assert/strict'
import {nearestSamples,numberText} from '../src/utils/mapInteraction.js'
import {displayTrace,wgsToGcj} from '../src/utils/mapCoordinates.js'
import {simulationColor,playbackClock} from '../src/utils/playback.js'

test('点击普通采样位置即可命中，不限于热点或每30个点',()=>{
  const points=Array.from({length:100},(_,id)=>({id,coord:[id*10,0]}))
  assert.deepEqual(nearestSamples(points,{x:290,y:1},([x,y])=>({x,y})),[29])
})
test('远离观测位置不返回伪造点，同位置多记录保留独立ID',()=>{
  const points=[{id:1,coord:[10,20]},{id:2,coord:[10,20]},{id:3,coord:[11,21]}]
  const project=([x,y])=>({x,y})
  assert.deepEqual(nearestSamples(points,{x:500,y:500},project),[])
  assert.deepEqual(nearestSamples(points,{x:10,y:20},project),[1,2,3])
})
test('高德点查坐标同步转换，记录值和ID保持关联',()=>{
  const sample={id:4,coord:[23.17,113.12],values:{dissolved_oxygen:0}}
  const converted=displayTrace({samples:[sample]},true).samples[0]
  assert.deepEqual(converted.coord,wgsToGcj(sample.coord))
  assert.equal(converted.id,4);assert.equal(converted.values.dissolved_oxygen,0)
  assert.deepEqual(sample.coord,[23.17,113.12])
})
test('零值显示为零，缺测和非有限值显示为空',()=>{
  assert.equal(numberText(0),'0')
  for(const value of [null,undefined,NaN,Infinity])assert.equal(numberText(value),'—')
})
test('模拟色标在全时段固定，低DO方向相反，常数场颜色有效',()=>{
  const scale={min:0,max:10,inverse:false}
  assert.notEqual(simulationColor(0,scale),simulationColor(10,scale))
  assert.equal(simulationColor(0,{...scale,inverse:true}),simulationColor(10,scale))
  assert.doesNotMatch(simulationColor(5,{min:5,max:5}),/NaN|Infinity/)
})
test('播放器变速替换计时器，暂停与卸载清理，多个实例互不干扰',()=>{
  const active=new Map();let id=0,steps=0
  const scheduler={set:(fn,ms)=>{active.set(++id,{fn,ms});return id},clear:i=>active.delete(i)}
  const a=playbackClock(()=>steps++,scheduler),b=playbackClock(()=>steps+=10,scheduler)
  a.start();b.start(2);assert.equal(active.size,2)
  a.start(4);assert.equal(active.size,2)
  assert.equal(active.get(3).ms,125)
  active.get(3).fn();assert.equal(steps,1)
  a.stop();assert.equal(active.size,1)
  b.stop();a.stop();assert.equal(active.size,0)
})
