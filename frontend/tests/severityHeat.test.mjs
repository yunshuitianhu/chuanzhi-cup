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

test('色标上限在插值前应用，极端值不会制造最高色阶平台', () => {
  const capped=severityRaster([[15,20,8],[25,20,1]],41,41,18)
  const extreme=severityRaster([[15,20,560],[25,20,1]],41,41,18)
  assert.deepEqual(extreme,capped)
  assert.notDeepEqual(Array.from(extreme.slice((20*41+20)*4,(20*41+20)*4+3)),severityColor(8))
})

test('边缘透明度连续淡出，无原来的大面积硬平台', () => {
  const raster=severityRaster([[30,30,3]],61,61,24)
  const alpha=x=>raster[(30*61+x)*4+3]
  assert.equal(alpha(30),220)
  assert.equal(alpha(54),0)
  for(let x=30;x<54;x++) assert.ok(alpha(x)>=alpha(x+1))
  assert.ok(alpha(31)<alpha(30))
  assert.ok(alpha(53)<=2)
  assert.throws(()=>severityRaster([],1,1,0),RangeError)
})

test('高低值之间有足够的中间色，远距离缺测仍保持透明', () => {
  const raster=severityRaster([[20,30,1],[40,30,8],[120,30,3]],151,61,24)
  const colors=new Set()
  for(let x=20;x<=40;x++) colors.add(Array.from(raster.slice((30*151+x)*4,(30*151+x)*4+3)).join(','))
  assert.ok(colors.size>=18)
  assert.equal(raster[(30*151+80)*4+3],0)
})
