import test from 'node:test'
import assert from 'node:assert/strict'
import { severityColor, severityRaster } from '../src/utils/severityHeat.js'

test('重复采样不会把相同严重程度累加成深红', () => {
  const point = [10,10,1.5]
  const one=severityRaster([point],21,21), many=severityRaster(Array(50).fill(point),21,21)
  // 浮点求和在半整数色值处可产生 1 个 RGB 量化单位的差异。
  assert.ok(one.every((v,i)=>Math.abs(v-many[i])<=1))
})
test('颜色连续变化，有界，低值不因本次最大值低而变红', () => {
  assert.notDeepEqual(severityColor(1.5), severityColor(5))
  assert.deepEqual(severityColor(100), severityColor(8))
  assert.deepEqual(severityColor(-1), severityColor(0))
  const a=severityColor(2), b=severityColor(2.01)
  assert.ok(a.every((v,i)=>Math.abs(v-b[i])<=3))
})
test('无观测支撑区域透明、无效观测跳过', () => {
  const image=severityRaster([[10,10,3],[NaN,0,1]],30,30,3)
  assert.equal(image[3],0)
  assert.ok(image[(10*30+10)*4+3]>0)
})
