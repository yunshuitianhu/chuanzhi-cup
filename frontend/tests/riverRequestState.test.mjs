import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {parse,compileScript} from '@vue/compiler-sfc'
import {createRenderer,nextTick,reactive} from 'vue'

const file=new URL('../src/views/RiverView.vue',import.meta.url)
const {descriptor}=parse(await readFile(file,'utf8'))
const compiled=compileScript(descriptor,{id:'river-state-regression'})
const fixtureURL='data:text/javascript,'+encodeURIComponent('export const api=globalThis.__riverApi;export const useSessionStore=()=>globalThis.__riverStore;export const toastErr=()=>{};export const toastOk=()=>{};export default {render:()=>null}')
const code=compiled.content.replace(/from ['"]([^'"]+)['"]/g,(_match,source)=>{
  const target=source==='vue'?import.meta.resolve('vue'):source==='../utils/projectRestore'
    ?new URL('../src/utils/projectRestore.js',import.meta.url).href:fixtureURL
  return `from ${JSON.stringify(target)}`
})
const renderer=createRenderer({createElement:tag=>({tag,children:[]}),createText:text=>({text}),createComment:text=>({text}),
  insert(node,parent){parent.children.push(node)},remove(){},setText(){},setElementText(){},parentNode:()=>null,nextSibling:()=>null,patchProp(){}})
let instance=0
async function mount(method,options={}){
  let resolve,reject,sent
  const promise=new Promise((done,fail)=>{resolve=done;reject=fail})
  const projects=options.projects||(async()=>({model:null,river_ui:{}}))
  globalThis.__riverApi={projects,[method]:payload=>{sent=payload;return promise}}
  globalThis.__riverStore=reactive({workspaceRevision:1,switchingProject:false,riverForm:null,
    projectState:options.projectState||{current:null},
    refreshCount:0,status:{analysis_revision:1,has_data:!!options.hasData},
    async refreshStatus(){this.refreshCount++}})
  // A fresh dependency URL prevents sharing stubs between independent mounts.
  const uniqueCode=code.replaceAll(fixtureURL,fixtureURL+`#instance-${++instance}`)
  const {default:Component}=await import('data:text/javascript;base64,'+Buffer.from(uniqueCode).toString('base64'))
  Component.render=()=>null
  const app=renderer.createApp(Component), vm=app.mount({children:[]})
  await nextTick();await nextTick()
  return {state:vm.$.setupState,store:globalThis.__riverStore,resolve,reject,app,sent:()=>sent}
}

for(const [method,action,key,newValue,result] of [
  ['riverInit','runInit','length_m',2000,'initResult'],
  ['riverHydro','runHydro','upstream_flow_m3s',20,'hydroResult'],
  ['riverPollution','runPollution','load_kg',500,'pollutionResult'],
])test(`${action} 不接纳参数编辑前的延迟结果`,async()=>{
  const view=await mount(method)
  try {
    const pending=view.state[action]()
    view.state.form[key]=newValue;await nextTick()
    view.resolve({marker:'old'});await pending
    assert.equal(view.state[result],null)
    assert.equal(view.state.busy,'')
    assert.notEqual(view.sent().parameters?.[key]??view.sent()[key],newValue)
  }finally{view.app.unmount()}
})

test('未编辑的结果正常接纳，切换工作区或卸载不接纳旧请求',async()=>{
  for(const mode of ['unchanged','workspace','unmount']){
    const view=await mount('riverPollution')
    const pending=view.state.runPollution()
    if(mode==='workspace')view.store.workspaceRevision++
    if(mode==='unmount')view.app.unmount()
    view.resolve({marker:'result'});await pending
    assert.equal(view.state.pollutionResult?.marker,mode==='unchanged'?'result':undefined)
    if(mode!=='unmount')view.app.unmount()
  }
})

test('与水动力无关的污染负荷编辑不丢弃水动力响应',async()=>{
  const view=await mount('riverHydro')
  try {
    const pending=view.state.runHydro();view.state.form.load_kg=123;await nextTick()
    view.resolve({marker:'hydro'});await pending
    assert.equal(view.state.hydroResult.marker,'hydro')
  }finally{view.app.unmount()}
})

for(const [method,action] of [
  ['riverInit','runInit'],['riverHydro','runHydro'],['riverPollution','runPollution'],
])test(`${action} 成功后同步工作台状态，避免旧 results_revision`,async()=>{
  const view=await mount(method)
  try {
    const pending=view.state[action]()
    view.resolve({marker:'success'});await pending
    assert.equal(view.store.refreshCount,1)
  }finally{view.app.unmount()}
})

for(const [method,action,key,newValue] of [
  ['riverInit','runInit','length_m',2000],
  ['riverHydro','runHydro','upstream_flow_m3s',20],
  ['riverPollution','runPollution','load_kg',500],
])test(`${action} 参数编辑后的延迟失败不标记当前步骤`,async()=>{
  const view=await mount(method)
  try {
    const pending=view.state[action]()
    view.state.form[key]=newValue;await nextTick()
    view.reject(new Error('旧请求失败'));await pending
    assert.equal(view.state.failedStep,'')
    assert.equal(view.state.runNotice,'')
    assert.equal(view.state.busy,'')
    assert.match(view.state.changedNotice,/已变化/)
  }finally{view.app.unmount()}
})

test('未编辑请求的失败仍标记当前步骤',async()=>{
  const view=await mount('riverHydro')
  try {
    const pending=view.state.runHydro()
    view.reject(new Error('受控失败'));await pending
    assert.equal(view.state.failedStep,'hydro')
    assert.match(view.state.runNotice,/水动力模拟失败/)
    assert.equal(view.state.busy,'')
  }finally{view.app.unmount()}
})

const detectProjects=async()=>({model:null,river_ui:{form:{length_m:1000}},current:{id:'project-A'}})
test('同一项目的自动参数建议正常应用',async()=>{
  const view=await mount('riverParams',{hasData:true,projects:detectProjects})
  try {
    const pending=view.state.detectParams()
    assert.equal(view.state.busy,'params')
    view.resolve({river_name:'受控河道',length_m:1500,width_avg_m:30,depth_avg_m:2,
      longitudinal_slope:.0001,bed_elevation_avg_m:0,high_water_mean_m:2,ammonia_nh3_mgL:1,
      dissolved_oxygen_mgL:5,mannings_n:.03,avg_discharge_m3s:10,
      param_sources:{},param_confidence:{}})
    await pending
    assert.equal(view.state.params.river_name,'受控河道')
    assert.equal(view.state.form.length_m,1500)
    assert.equal(view.state.form.upstream_flow_m3s,10)
    assert.equal(view.state.form.chainage_m,750)
    assert.equal(view.state.busy,'')
  }finally{view.app.unmount()}
})

test('自动参数建议迟到的成功不得写入已切换项目',async()=>{
  const view=await mount('riverParams',{hasData:true,projects:detectProjects})
  try {
    const pending=view.state.detectParams()
    view.store.projectState.current={id:'project-B'}
    view.store.workspaceRevision++
    view.resolve({river_name:'旧项目河道',length_m:9999,avg_discharge_m3s:99,
      width_avg_m:30,depth_avg_m:2,longitudinal_slope:.0001,bed_elevation_avg_m:0,
      high_water_mean_m:2,ammonia_nh3_mgL:1,dissolved_oxygen_mgL:5,mannings_n:.03,
      param_sources:{},param_confidence:{}})
    await pending
    assert.equal(view.state.params,null)
    assert.equal(view.state.form.length_m,1000)
    assert.equal(view.state.paramsNotice,'')
    assert.equal(view.state.busy,'')
  }finally{view.app.unmount()}
})

test('自动参数建议迟到的失败不得写入已切换项目',async()=>{
  const view=await mount('riverParams',{hasData:true,projects:detectProjects})
  try {
    const pending=view.state.detectParams()
    view.store.workspaceRevision++
    view.reject(new Error('旧项目检测失败'));await pending
    assert.equal(view.state.paramsNotice,'')
    assert.equal(view.state.busy,'')
  }finally{view.app.unmount()}
})
