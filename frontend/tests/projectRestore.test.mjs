import test from 'node:test'
import assert from 'node:assert/strict'
import { restoreRiverWorkspace } from '../src/utils/projectRestore.js'

const defaults = { river_name:'默认', length_m:1000, n_cross_sections:50, upstream_flow_m3s:6,
  high_water_mean_m:2, duration_h:6, tidal_amplitude_m:0 }
function fixture() {
  return { model:{config:{river_name:'河流B',length_m:800,avg_discharge_m3s:4,high_water_mean_m:1.5},
    cross_section_count:5,initialized:true,has_hydro:true,has_wq:true,
    hydro_params:{upstream_flow_m3s:4,downstream_stage_m:1.5,duration_h:.5,tidal_amplitude_m:0},
    wq_params:{load_kg:1}},river_ui:{init:{status:'init'},hydro:{status:'hydro'},pollution:{status:'wq'}} }
}
test('项目参数优先于默认模板，恢复已运行结果',()=>{
  const restored=restoreRiverWorkspace(fixture(),null,defaults)
  assert.equal(restored.form.river_name,'河流B')
  assert.equal(restored.form.length_m,800)
  assert.equal(restored.form.duration_h,.5)
  assert.equal(restored.hydro.status,'hydro')
  assert.equal(restored.hydro.工况,'恒定流')
  assert.match(restored.hydro.结果可信度,/不代表现场率定/)
  assert.equal(restored.pollution.status,'wq')
})
test('保存的未应用几何草稿不能沿用旧模型按钮状态',()=>{
  const restored=restoreRiverWorkspace(fixture(),{length_m:1200},defaults)
  assert.equal(restored.form.length_m,1200)
  assert.equal(restored.init,null);assert.equal(restored.hydro,null);assert.equal(restored.pollution,null)
})
test('边界草稿更改保留初始化，要求重新运行水动力',()=>{
  const restored=restoreRiverWorkspace(fixture(),{upstream_flow_m3s:20},defaults)
  assert.ok(restored.init);assert.equal(restored.hydro,null);assert.equal(restored.pollution,null)
})
test('空项目不继承其它项目结果',()=>{
  const restored=restoreRiverWorkspace({model:null,river_ui:{}},null,defaults)
  assert.equal(restored.form.river_name,'默认');assert.equal(restored.init,null)
})
test('旧结果恢复原网格，新结果恢复实际细分数，草稿不能冒用旧结果',()=>{
  const saved=fixture()
  assert.equal(restoreRiverWorkspace(saved,null,{...defaults,wq_refinement:4}).form.wq_refinement,1)
  saved.model.wq_params.wq_refinement=8
  const restored=restoreRiverWorkspace(saved,null,{...defaults,wq_refinement:4})
  assert.equal(restored.form.wq_refinement,8);assert.equal(restored.pollution.status,'wq')
  const draft=restoreRiverWorkspace(saved,{wq_refinement:4},{...defaults,wq_refinement:4})
  assert.ok(draft.hydro);assert.equal(draft.pollution,null)
})
