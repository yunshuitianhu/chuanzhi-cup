import test from 'node:test'
import assert from 'node:assert/strict'
import { wgsToGcj, gcjToWgs, displayTrace } from '../src/utils/mapCoordinates.js'
import { staticViewport } from '../src/utils/staticViewport.js'

test('显示坐标与后端geo_tools三地计算结果一致，逆变换可恢复',()=>{
  for(const [point, expected] of [
    [[23.169266,113.121774],[23.166773596546975,113.127266676311]],
    [[39.9,116.4],[39.901403529849404,116.40624278491117]],
    [[30.67,104.06],[30.66757487197054,104.06249640425462]],
  ]) {
    const actual=wgsToGcj(point), restored=gcjToWgs(actual)
    actual.forEach((v,i)=>assert.ok(Math.abs(v-expected[i])<1e-10))
    restored.forEach((v,i)=>assert.ok(Math.abs(v-point[i])<1e-7))
  }
})
test('境外底图坐标保持不变',()=>{
  for(const p of [[51.5,-.1],[-33.8,151.2],[0,0]]) {
    assert.deepEqual(wgsToGcj(p),p); assert.deepEqual(gcjToWgs(p),p)
  }
})
test('所有覆盖物同步转换，保留污染强度与原始数据',()=>{
  const p=[23.169266,113.121774]
  const original={track:[p],heat:[[...p,3.4]],clusters:[{center:p,count:7}],top5:[{coord:p,rank:2}]}
  const before=JSON.stringify(original), result=displayTrace(original,true), expected=wgsToGcj(p)
  assert.deepEqual(result.track[0],expected)
  assert.deepEqual(result.heat[0],[...expected,3.4])
  assert.deepEqual(result.clusters[0],{center:expected,count:7})
  assert.deepEqual(result.top5[0],{coord:expected,rank:2})
  assert.equal(JSON.stringify(original),before)
  assert.equal(displayTrace(original,false),original)
})
test('普通窗口请求不改变缩放或范围',()=>{
  assert.deepEqual(staticViewport(640,460,15),{zoom:15,width:640,height:460,displayWidth:640,displayHeight:460})
})
test('大窗口降低取图级别，像素覆盖与地理比例一致',()=>{
  for(const [w,h] of [[1400,460],[2801,1701],[512,2048]]) {
    const spec=staticViewport(w,h,15), scale=2**(15-spec.zoom)
    assert.ok(spec.width<=1024 && spec.height<=1024)
    assert.equal(spec.displayWidth,spec.width*scale)
    assert.equal(spec.displayHeight,spec.height*scale)
    assert.ok(spec.displayWidth>=w && spec.displayWidth<w+scale)
    assert.ok(spec.displayHeight>=h && spec.displayHeight<h+scale)
  }
})
test('低级别和极端尺寸仍遵守服务的尺寸与级别限制',()=>{
  for(const [w,h,z] of [[3000,2000,1],[1,1,1],[0,0,3]]) {
    const spec=staticViewport(w,h,z)
    assert.ok(spec.zoom>=1 && spec.width>=1 && spec.height>=1 && spec.width<=1024 && spec.height<=1024)
  }
})
