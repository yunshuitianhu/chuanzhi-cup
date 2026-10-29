import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {parse,compileScript} from '@vue/compiler-sfc'
import {createRenderer,nextTick,reactive} from 'vue'

const file=new URL('../src/components/VisualEvidence.vue',import.meta.url)
const {descriptor}=parse(await readFile(file,'utf8'))
const compiled=compileScript(descriptor,{id:'visual-evidence-regression'})
const fixtureURL='data:text/javascript,'+encodeURIComponent('export const api=globalThis.__veApi;export const useSessionStore=()=>globalThis.__veStore;')
const code=compiled.content.replace(/from ['"]([^'"]+)['"]/g,(_match,source)=>{
  const target=source==='vue'?import.meta.resolve('vue'):fixtureURL
  return `from ${JSON.stringify(target)}`
})
const renderer=createRenderer({createElement:tag=>({tag,children:[]}),createText:text=>({text}),createComment:text=>({text}),
  insert(node,parent){parent.children.push(node)},remove(){},setText(){},setElementText(){},parentNode:()=>null,nextSibling:()=>null,patchProp(){}})
let instance=0
const revision='a'.repeat(64)
function context(){
  return reactive({kind:'video_frame',title:'视频第2帧',
    selection:{frame_id:1,video_revision:revision}})
}
async function mount(props={}){
  const calls=[]
  let resolve,reject
  const promise=new Promise((done,fail)=>{resolve=done;reject=fail})
  globalThis.__veApi={visionObserve:(payload,signal)=>{
    const call={payload,signal,resolve,reject}
    calls.push(call)
    signal?.addEventListener('abort',()=>{
      const error=new Error('aborted');error.name='AbortError';reject(error)
    },{once:true})
    return promise
  }}
  globalThis.__veStore=reactive({ready:true,loading:false,switchingProject:false,
    workspaceRevision:1,projectState:{current:{id:'project-A'}},
    status:{analysis_revision:1,results_revision:0}})
  const uniqueCode=code.replaceAll(fixtureURL,fixtureURL+`#instance-${++instance}`)
  const {default:Component}=await import('data:text/javascript;base64,'+Buffer.from(uniqueCode).toString('base64'))
  Component.render=()=>null
  const app=renderer.createApp(Component,{context:context(),disabled:false,...props})
  const vm=app.mount({children:[]})
  await nextTick();await nextTick()
  return {state:vm.$.setupState,store:globalThis.__veStore,app,calls,resolve,reject}
}

test('选择来源不会自动发送，点击后才提交当前帧和版本',async()=>{
  const view=await mount()
  try {
    assert.equal(view.calls.length,0)
    const pending=view.state.send()
    assert.equal(view.calls.length,1)
    assert.equal(view.calls[0].payload.kind,'video_frame')
    assert.equal(view.calls[0].payload.frame_id,1)
    assert.equal(view.calls[0].payload.video_revision,revision)
    assert.equal(view.state.busy,true)
    view.resolve({ok:true,model:'controlled',cached:false,elapsed_s:1,
      source:{kind:'video_frame',label:'视频第2帧',sha256:'b'.repeat(64),frame_number:2,time_sec:5},
      observations:[{visible:'可见泡沫',where:'右侧',uncertainty:['无法判断持续'],frame_id:1,frame_number:2,time_sec:5}],
      not_visible:[],quality_notes:[],limits:['不给出浓度']})
    await pending
    assert.equal(view.state.busy,false)
    assert.equal(view.state.result.observations[0].visible,'可见泡沫')
  }finally{view.app.unmount()}
})

test('图片附件使用附件版本提交',async()=>{
  const view=await mount({context:{kind:'image_asset',title:'截图',selection:{asset_id:'c'.repeat(32),asset_revision:revision}}})
  try {
    const pending=view.state.send()
    assert.equal(view.calls[0].payload.kind,'image_asset')
    assert.equal(view.calls[0].payload.asset_id,'c'.repeat(32))
    assert.equal(view.calls[0].payload.asset_revision,revision)
    view.resolve({ok:true,model:'controlled',cached:false,elapsed_s:1,source:{kind:'image_asset',label:'截图',sha256:'d'.repeat(64)},
      observations:[],not_visible:[],quality_notes:[],limits:[]})
    await pending
    assert.equal(view.state.result.source.kind,'image_asset')
  }finally{view.app.unmount()}
})

test('来源版本变化后的迟到结果不写入页面',async()=>{
  const view=await mount()
  try {
    const pending=view.state.send()
    view.store.workspaceRevision++
    view.resolve({ok:true,model:'controlled',cached:false,elapsed_s:1,
      source:{kind:'video_frame',sha256:'b'.repeat(64)},observations:[],
      not_visible:[],quality_notes:[],limits:[]})
    await pending
    assert.equal(view.state.result,null)
  }finally{view.app.unmount()}
})

test('模型失败显示错误且不伪造观察结果',async()=>{
  const view=await mount()
  try {
    const pending=view.state.send()
    view.reject(new Error('视觉服务不可用'))
    await pending
    assert.equal(view.state.result,null)
    assert.match(view.state.error,/视觉服务不可用/)
    assert.equal(view.state.busy,false)
  }finally{view.app.unmount()}
})

test('停止等待会取消请求并保留未写入提示',async()=>{
  const view=await mount()
  try {
    view.state.send()
    await nextTick()
    view.state.cancel()
    await nextTick()
    assert.equal(view.state.busy,false)
    assert.equal(view.state.result,null)
    assert.match(view.state.notice,/已停止等待/)
  }finally{view.app.unmount()}
})
