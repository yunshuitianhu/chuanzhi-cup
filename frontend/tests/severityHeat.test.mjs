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

test('中心清晰、边缘连续淡出，不形成圆形硬边', () => {
  const raster=severityRaster([[30,30,3]],61,61,24)
  const alpha=x=>raster[(30*61+x)*4+3]
  assert.equal(alpha(30),240)
  assert.equal(alpha(54),0)
  for(let x=30;x<54;x++) assert.ok(alpha(x)>=alpha(x+1))
  assert.ok(alpha(38)<alpha(30))
  assert.ok(alpha(53)<=2)
  assert.throws(()=>severityRaster([],1,1,0),RangeError)
})

test('可调透明度只改变显示强度，不改变严重程度颜色',()=>{
  const a=severityRaster([[10,10,3]],21,21,10,1)
  const b=severityRaster([[10,10,3]],21,21,10,.5)
  const center=(10*21+10)*4
  assert.deepEqual(a.slice(center,center+3),b.slice(center,center+3))
  assert.equal(a[center+3],255)
  assert.equal(b[center+3],128)
  assert.throws(()=>severityRaster([],1,1,2,2),RangeError)
})

test('收窄色带保留中心颜色，减少离轨区域覆盖',()=>{
  const points=Array.from({length:60},(_,i)=>[i+10,30,3])
  const wide=severityRaster(points,80,61,24),narrow=severityRaster(points,80,61,12)
  const center=(30*80+40)*4,edge=(10*80+40)*4
  assert.deepEqual(wide.slice(center,center+3),narrow.slice(center,center+3))
  assert.ok(wide[edge+3]>0)
  assert.equal(narrow[edge+3],0)
  for(let x=15;x<65;x++)assert.equal(narrow[(30*80+x)*4+3],240)
})

test('高低值之间有足够的中间色，远距离缺测仍保持透明', () => {
  const raster=severityRaster([[20,30,1],[40,30,8],[120,30,3]],151,61,24)
  const colors=new Set()
  for(let x=20;x<=40;x++) colors.add(Array.from(raster.slice((30*151+x)*4,(30*151+x)*4+3)).join(','))
  assert.ok(colors.size>=18)
  assert.equal(raster[(30*151+80)*4+3],0)
})
