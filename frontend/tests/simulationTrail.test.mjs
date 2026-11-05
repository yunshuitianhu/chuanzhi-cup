import test from 'node:test'
import assert from 'node:assert/strict'
import { trailProfile, trailSegments, trailColor } from '../src/utils/simulationTrail.js'

function sample() { return {
  constituent:'ammonia',source:{source_start_h:0},times:[0,1,2,3],
  mapping:{available:true,positions:[[30,120],[30,120.001],[30,120.002],[30,120.003]]},
  concentrations:[[2,2,2,2],[2,6,2,2],[2,3,5,2],[2,2,2,2]],
} }
test('非零背景初始不绘扩散，固定色标使用全时段相对初始增量',()=>{
  const sim=sample(), profile=trailProfile(sim)
  assert.equal(profile.max,4)
  assert.deepEqual(trailSegments(sim,0,profile),[])
  assert.equal(trailSegments(sim,1,profile).length,2)
})
test('线段按增量边界裁切，不把零值区间全部画满',()=>{
  const sim=sample(), segments=trailSegments(sim,1)
  assert.ok(segments[0].from[1]>120)
  assert.ok(segments[1].to[1]<120.002)
  assert.ok(segments.every(s=>s.to[1]<=120.002))
})
test('回退只用当前帧，消散后清空，复位不留未来轨迹',()=>{
  const sim=sample(), before=JSON.stringify(sim), early=trailSegments(sim,1)
  assert.notDeepEqual(trailSegments(sim,2),early)
  assert.deepEqual(trailSegments(sim,1),early)
  assert.deepEqual(trailSegments(sim,3),[])
  assert.deepEqual(trailSegments(sim,0),[])
  assert.equal(JSON.stringify(sim),before)
})
test('延迟源开始之前仅源位置，DO采用下降量，不把0当缺测',()=>{
  const sim=sample();sim.source.source_start_h=1
  assert.deepEqual(trailSegments(sim,1),[])
  sim.constituent='do';sim.source.source_start_h=0
  sim.concentrations=[[7,7,7,7],[7,0,7,7]];sim.times=[0,1]
  assert.equal(trailProfile(sim).max,7)
  assert.equal(trailSegments(sim,1).length,2)
})
test('无配准、非法帧、缺测或非法坐标不补造扩散',()=>{
  const sim=sample();sim.mapping.available=false
  assert.deepEqual(trailSegments(sim,1),[])
  sim.mapping.available=true
  assert.deepEqual(trailSegments(sim,99),[])
  sim.concentrations[1]=[2,NaN,2,2]
  assert.deepEqual(trailSegments(sim,1),[])
  sim.concentrations[1]=[2,6,2,2];sim.mapping.positions[1]=[999,120]
  assert.deepEqual(trailSegments(sim,1),[])
})
test('无增量不绘色带，低/高值渐变和越界色值有限',()=>{
  const sim=sample();sim.concentrations=sim.concentrations.map(()=>[2,2,2,2])
  assert.deepEqual(trailSegments(sim,1),[])
  assert.notEqual(trailColor(0),trailColor(1))
  assert.equal(trailColor(2),trailColor(1))
})
