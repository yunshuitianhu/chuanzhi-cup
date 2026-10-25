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
async function mount(method){
  let resolve,sent
  const promise=new Promise(done=>{resolve=done})
  globalThis.__riverApi={projects:async()=>({model:null,river_ui:{}}),[method]:payload=>{sent=payload;return promise}}
  globalThis.__riverStore=reactive({workspaceRevision:1,switchingProject:false,riverForm:null,status:{analysis_revision:1,has_data:false}})
  // A fresh dependency URL prevents sharing stubs between independent mounts.
  const uniqueCode=code.replaceAll(fixtureURL,fixtureURL+`#instance-${++instance}`)
  const {default:Component}=await import('data:text/javascript;base64,'+Buffer.from(uniqueCode).toString('base64'))
  Component.render=()=>null
  const app=renderer.createApp(Component), vm=app.mount({children:[]})
  await nextTick();await nextTick()
  return {state:vm.$.setupState,store:globalThis.__riverStore,resolve,app,sent:()=>sent}
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
