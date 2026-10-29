import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {parse,compileScript} from '@vue/compiler-sfc'
import {createRenderer,nextTick,reactive} from 'vue'

const file=new URL('../src/components/VisualSequenceEvidence.vue',import.meta.url)
const {descriptor}=parse(await readFile(file,'utf8'))
const compiled=compileScript(descriptor,{id:'visual-sequence-regression'})
const fixtureURL='data:text/javascript,'+encodeURIComponent('export const api=globalThis.__seqApi;export const useSessionStore=()=>globalThis.__seqStore;')
const code=compiled.content.replace(/from ['"]([^'"]+)['"]/g,(_match,source)=>{
  const target=source==='vue'?import.meta.resolve('vue'):fixtureURL
  return `from ${JSON.stringify(target)}`
})
const renderer=createRenderer({createElement:tag=>({tag,children:[]}),createText:text=>({text}),createComment:text=>({text}),
  insert(node,parent){parent.children.push(node)},remove(){},setText(){},setElementText(){},parentNode:()=>null,nextSibling:()=>null,patchProp(){}})
let instance=0
const revision='a'.repeat(64)
function frame(id,status='usable',flags=[]){
  return {image_base64:'aW1hZ2U=',media_type:'image/jpeg',
    frame:{id,time_sec:id*5,quality_status:status,quality_flags:flags,
      quality_messages:flags.length?['受控质量提示']:[],eligible:status==='usable'}}
}
async function mount(options={}){
  const frameCalls=[],observeCalls=[]
  globalThis.__seqApi={
    videoFrame:(id,rev,signal)=>{
      frameCalls.push({id,rev,signal})
      return options.frameHandler?options.frameHandler(id,rev,signal):Promise.resolve(frame(id))
    },
    visionObserve:(payload,signal)=>{
      observeCalls.push({payload,signal})
      if(options.observeHandler)return options.observeHandler(payload,signal)
      return Promise.resolve({ok:true,kind:'video_frames',model:'controlled',cached:false,elapsed_s:1,
        frame_ids:payload.frame_ids,frames:payload.frame_ids.map((id,index)=>({index:index+1,
          source:{frame_id:id,frame_number:id+1,time_sec:id*5,sha256:'b'.repeat(64)},
          source_quality:{quality_status:'usable',quality_flags:[],quality_messages:[]},
          observations:[{visible:'可见纹理',where:'全画面',uncertainty:[]}]})),
        changes:[],static_sequence:false,time_start_s:payload.frame_ids[0]*5,
        time_end_s:payload.frame_ids.at(-1)*5,not_visible:[],quality_notes:[],limits:['限制']})
    }
  }
  globalThis.__seqStore=reactive({ready:true,loading:false,switchingProject:false,
    workspaceRevision:1,projectState:{current:{id:'project-A'}},
    status:{analysis_revision:1,results_revision:0}})
  const uniqueCode=code.replaceAll(fixtureURL,fixtureURL+`#instance-${++instance}`)
  const {default:Component}=await import('data:text/javascript;base64,'+Buffer.from(uniqueCode).toString('base64'))
  Component.render=()=>null
  const app=renderer.createApp(Component,{total:6,revision,current:1,disabled:false,...options.props})
  const vm=app.mount({children:[]})
  await nextTick();await nextTick()
  return {state:vm.$.setupState,store:globalThis.__seqStore,app,frameCalls,observeCalls}
}

test('序列不会自动预览或发送；预览后按已选帧提交',async()=>{
  const view=await mount()
  try {
    assert.equal(view.frameCalls.length,0)
    assert.equal(view.observeCalls.length,0)
    await view.state.previewFrames()
    assert.deepEqual(view.frameCalls.map(call=>call.id),[1,2,3,4])
    assert.equal(view.state.preview.length,4)
    assert.equal(view.state.canSend,true)
    await view.state.sendSequence()
    assert.equal(view.observeCalls.length,1)
    assert.equal(view.observeCalls[0].payload.kind,'video_frames')
    assert.deepEqual(view.observeCalls[0].payload.frame_ids,[1,2,3,4])
    assert.equal(view.observeCalls[0].payload.video_revision,revision)
    assert.equal(view.state.result.frames.length,4)
    assert.equal(view.state.result.kind,'video_frames')
  }finally{view.app.unmount()}
})

test('预览含人工排除帧时不能发送',async()=>{
  const view=await mount({frameHandler:id=>Promise.resolve(
    id===2?frame(id,'needs_review',['manual_exclusion']):frame(id))})
  try {
    await view.state.previewFrames()
    assert.equal(view.state.sendable,false)
    await view.state.sendSequence()
    assert.equal(view.observeCalls.length,0)
    assert.match(view.state.notice,/人工排除/)
  }finally{view.app.unmount()}
})

test('发送期间工作区变化时丢弃迟到结果',async()=>{
  let resolve
  const pending=new Promise(done=>{resolve=done})
  const view=await mount({observeHandler:()=>pending})
  try {
    await view.state.previewFrames()
    const sending=view.state.sendSequence()
    view.store.workspaceRevision++
    resolve({ok:true,kind:'video_frames',model:'controlled',cached:false,elapsed_s:1,
      frame_ids:[1,2,3,4],frames:[],changes:[],static_sequence:false,not_visible:[],
      quality_notes:[],limits:[]})
    await sending
    assert.equal(view.state.result,null)
  }finally{view.app.unmount()}
})

test('停止等待会取消发送并保留未写入提示',async()=>{
  const view=await mount({observeHandler:(payload,signal)=>new Promise((resolve,reject)=>{
    signal?.addEventListener('abort',()=>{
      const error=new Error('aborted');error.name='AbortError';reject(error)
    },{once:true})
  })})
  try {
    await view.state.previewFrames()
    const sending=view.state.sendSequence()
    await nextTick()
    view.state.cancel()
    await sending
    assert.equal(view.state.result,null)
    assert.equal(view.state.busy,'')
    assert.match(view.state.notice,/已停止等待/)
  }finally{view.app.unmount()}
})

test('超出视频范围的序列不能预览',async()=>{
  const view=await mount({props:{total:6,current:1}})
  try {
    view.state.start=4
    view.state.count=4
    await view.state.previewFrames()
    assert.equal(view.frameCalls.length,0)
    assert.equal(view.state.canPreview,false)
  }finally{view.app.unmount()}
})
