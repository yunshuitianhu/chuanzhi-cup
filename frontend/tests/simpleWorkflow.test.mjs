import test from 'node:test'
import assert from 'node:assert/strict'
import {toSimpleSummary} from '../src/composables/useSimpleWorkflow.js'
const state=(status={})=>({status,projectState:{current:null}})
test('简易版区分未检测与已完成零预警，不依赖has_black_spots',()=>{
 assert.equal(toSimpleSummary(state({has_data:true})).warningPoints,null)
 const zero=toSimpleSummary(state({has_data:true,has_detection:true,has_black_spots:false,black_spot_count:0}))
 assert.equal(zero.warningPoints,0);assert.equal(zero.detection,'ready')
})
test('过期检测不当当前有效证据，数据清空不补造结果',()=>{
 assert.equal(toSimpleSummary(state({has_detection:true,detection_outdated:true,black_spot_count:6})).warningPoints,null)
 assert.equal(toSimpleSummary(state()).hasData,false)
 assert.equal(toSimpleSummary(state({has_detection:true})).warningPoints,null)
})
test('仿真身份和失败、部分AI结果保持真实状态',()=>{
 assert.equal(toSimpleSummary(state({data_identity:{kind:'synthetic'}})).identity,'仿真示例')
 for(const status of ['failed','partial'])assert.equal(toSimpleSummary(state({analysis_status:status})).ai,status)
 const s=state();s.chatRunning=true;assert.equal(toSimpleSummary(s).ai,'loading')
 assert.equal(toSimpleSummary(state({analysis_done:true})).ai,'ready')
})
