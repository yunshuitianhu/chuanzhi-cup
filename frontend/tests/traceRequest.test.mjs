import test from 'node:test'
import assert from 'node:assert/strict'
import {traceRequest} from '../src/utils/traceRequest.js'

test('空白坐标启用自动中心，0坐标保留为手动输入',()=>{
  for(const v of [null,undefined,'','  ']) assert.deepEqual(traceRequest(v,v,500),{payload:{radius_m:500},manual:false})
  assert.deepEqual(traceRequest(0,0,500),{payload:{lat:0,lon:0,radius_m:500},manual:true})
})
test('只填一项不能静默变成自动查询',()=>{
  assert.ok(traceRequest(30,'',500).error)
  assert.ok(traceRequest(null,120,500).error)
})
test('非法坐标或半径不给出可发送请求',()=>{
  for(const [a,b,r] of [[NaN,120,500],[30,Infinity,500],[91,120,500],[30,181,500],
    [null,null,''],[30,120,0],[30,120,50001],[30,120,500.5]]) assert.ok(traceRequest(a,b,r).error)
})
test('文本输入只在有效时转为数值',()=>{
  assert.deepEqual(traceRequest('30.12','120.34','1000'),{payload:{lat:30.12,lon:120.34,radius_m:1000},manual:true})
})
