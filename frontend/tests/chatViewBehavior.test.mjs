import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {pathToFileURL,fileURLToPath} from 'node:url'
import {createRequire} from 'node:module'

// Exercise the real Vue setup with isolated services and an optional fixture root.
const project = process.env.Q17_PROJECT_ROOT || fileURLToPath(new URL('../../',import.meta.url))
const require = createRequire(pathToFileURL(project + '/frontend/package.json'))
const compiler = await import(pathToFileURL(require.resolve('@vue/compiler-sfc')).href)
const vueURL = pathToFileURL(project + '/frontend/node_modules/vue/dist/vue.runtime.esm-bundler.js').href
const {createRenderer,nextTick,reactive} = await import(vueURL)
const source = await readFile(project + '/frontend/src/views/ChatView.vue', 'utf8')
const {descriptor} = compiler.parse(source)
const compiled = compiler.compileScript(descriptor, {id:'q17-chat-setup'}).content
let serial=0
const renderer=createRenderer({
  createElement:tag=>({tag,children:[]}),createText:text=>({text}),createComment:text=>({text}),
  insert(node,parent){parent.children.push(node)},remove(){},setText(){},setElementText(){},
  parentNode:()=>null,nextSibling:()=>null,patchProp(){},
})
const settle=async()=>{await nextTick();await new Promise(r=>setImmediate(r));await nextTick()}

async function mount() {
  const calls=[],scrolls=[],listeners=new Map(),frames=new Map()
  let selection={isCollapsed:true},frameId=0
  const store=reactive({
    ready:true,loading:false,chatRunning:false,chatMessages:[],chatStreaming:false,
    workspaceRevision:1,projectState:{current:{id:'a',name:'test'}},
    status:{has_data:true,analysis_revision:1,rows:6},config:{target_water_class:'V'},
    agentRun:null,agentRunAt:0,agentConnectionError:'',agentActiveId:null,
    selectedInvestigationId:'',investigationRevision:0,questionContext:null,questionContextNotice:'',chatDraft:'',
    refreshAgentRun:async()=>{store.chatRunning=false},
    refreshStatus:async()=>{},stopAgentRun:async()=>{},
    beginAgentStream(){if(store.chatRunning||store.loading||!store.ready)return false;store.chatRunning=true;return true},
    adoptAgentRun(){},
    clearQuestionContext(notice=''){store.questionContext=null;store.questionContextNotice=notice},
    attachMedia:async file=>{store.attached=file;return {id:'attached'}},
  })
  const saved={window:globalThis.window,document:globalThis.document,fetch:globalThis.fetch,
    request:globalThis.requestAnimationFrame,cancel:globalThis.cancelAnimationFrame}
  globalThis.window={getSelection:()=>selection}
  globalThis.document={addEventListener:(name,fn)=>listeners.set(name,fn),
    removeEventListener:(name,fn)=>{if(listeners.get(name)===fn)listeners.delete(name)},
    getElementById:id=>({scrollIntoView:options=>scrolls.push({id,options})})}
  globalThis.requestAnimationFrame=fn=>{frames.set(++frameId,fn);return frameId}
  globalThis.cancelAnimationFrame=id=>frames.delete(id)
  globalThis.fetch=async(_url,options)=>{calls.push(JSON.parse(options.body));return {ok:true,body:{},headers:{get:()=> 'run-test'}}}
  globalThis.__q17ChatStore=store
  const fixture='data:text/javascript,'+encodeURIComponent(
    "import {toRefs} from "+JSON.stringify(vueURL)+";export const storeToRefs=toRefs;"+
    "export const useSessionStore=()=>globalThis.__q17ChatStore;"+
    "export const sseHeaders=()=>({});export const adoptSessionFrom=()=>{};"+
    "export const toastErr=()=>{};export const consumeAgentStream=async()=>{};"+
    "export default {render:()=>null}")
  const code=compiled.replace(/from ['"]([^'"]+)['"]/g,(_match,dependency)=>{
    const target=dependency==='vue'?vueURL:
      ['../utils/chatScroll','../utils/questionContext'].includes(dependency)?pathToFileURL(project+'/frontend/src/utils/'+dependency.split('/').at(-1)+'.js').href:fixture
    return 'from '+JSON.stringify(target)
  })
  const {default:Component}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64')+'#'+(++serial))
  Component.render=()=>null
  const app=renderer.createApp(Component),vm=app.mount({children:[]})
  await settle()
  return {state:vm.$.setupState,store,calls,scrolls,listeners,frames,settle,
    setSelection(next){selection=next},
    close(){app.unmount();globalThis.window=saved.window;globalThis.document=saved.document;globalThis.fetch=saved.fetch;
      globalThis.requestAnimationFrame=saved.request;globalThis.cancelAnimationFrame=saved.cancel},
  }
}

function enter(extra={}) {
  return {key:'Enter',shiftKey:false,isComposing:false,keyCode:13,prevented:0,
    preventDefault(){this.prevented++},...extra}
}

test('Chinese IME Enter and legacy composing keyCode do not send or erase the draft',async()=>{
  const v=await mount()
  try {
    v.state.input='正在输入的问题'
    for(const event of [enter({isComposing:true}),enter({keyCode:229})]){
      v.state.composerKey(event);await v.settle()
      assert.equal(event.prevented,0);assert.equal(v.state.input,'正在输入的问题')
    }
    assert.equal(v.calls.length,0);assert.equal(v.store.chatMessages.length,0)
  }finally{v.close()}
})

test('Shift+Enter preserves the native newline and ordinary Enter sends the trimmed question',async()=>{
  const v=await mount()
  try {
    v.state.input='  解释这条记录  '
    const newline=enter({shiftKey:true})
    v.state.composerKey(newline);await v.settle()
    assert.equal(newline.prevented,0);assert.equal(v.calls.length,0)
    assert.equal(v.state.input,'  解释这条记录  ')
    const send=enter()
    v.state.composerKey(send);await v.settle()
    assert.equal(send.prevented,1)
    assert.equal(v.calls.length,1);assert.deepEqual(v.calls[0],{message:'解释这条记录',auto:false})
    assert.equal(v.state.input,'')
    assert.equal(v.store.chatMessages[0].content,'解释这条记录')
  }finally{v.close()}
})

for(const condition of ['chatRunning','loading','notReady']){
  test('send guard preserves the draft when '+condition,async()=>{
    const v=await mount()
    try {
      v.state.input='尚未发送的原始问题'
      if(condition==='notReady')v.store.ready=false
      else if(condition==='noData')v.store.status.has_data=false
      else v.store[condition]=true
      await v.state.send()
      assert.equal(v.state.input,'尚未发送的原始问题')
      assert.equal(v.calls.length,0);assert.equal(v.store.chatMessages.length,0)
    }finally{v.close()}
  })
}

test('blank draft never creates a model request',async()=>{
  const v=await mount()
  try{v.state.input=' \n ';await v.state.send();assert.equal(v.calls.length,0);assert.equal(v.store.chatMessages.length,0)}
  finally{v.close()}
})

test('only selections in the conversation pause follow and latest clears that selection',async()=>{
  const v=await mount()
  try {
    const inside={},outside={}
    v.state.scroller={scrollTop:0,scrollHeight:1000,clientHeight:300,contains:node=>node===inside}
    v.setSelection({isCollapsed:false,anchorNode:outside,focusNode:outside})
    v.state.selectionChanged()
    assert.equal(v.state.scrollState.following,true)
    let removed=0
    v.setSelection({isCollapsed:false,anchorNode:inside,focusNode:inside,removeAllRanges(){removed++}})
    v.state.selectionChanged()
    assert.equal(v.state.scrollState.following,false)
    await v.state.latest()
    assert.equal(removed,1);assert.equal(v.state.scrollState.following,true)
  }finally{v.close()}
})

test('manually closing the investigation section does not clear its visited state or chat draft',async()=>{
  const v=await mount()
  try {
    v.state.input='待补充的复测问题'
    v.state.openInvestigation({target:{open:true}})
    assert.equal(v.state.investigationVisited,true)
    v.state.openInvestigation({target:{open:false}})
    assert.equal(v.state.investigationVisited,true)
    assert.equal(v.state.input,'待补充的复测问题')
  }finally{v.close()}
})

test('unmount releases the document selection listener and pending animation frame',async()=>{
  const v=await mount()
  assert.equal(v.listeners.has('selectionchange'),true)
  v.close()
  assert.equal(v.listeners.size,0);assert.equal(v.frames.size,0)
})

test('adding a referenced investigation opens the section and preserves the question draft',async()=>{
  const v=await mount()
  try {
    const details={open:false}
    v.state.investigationDetails=details
    v.state.input='尚未发送的补充问题'
    v.store.selectedInvestigationId='task-new';v.store.investigationRevision++
    await v.settle()
    assert.equal(v.state.investigationVisited,true);assert.equal(details.open,true)
    assert.equal(v.state.input,'尚未发送的补充问题')
    assert.equal(v.scrolls.at(-1).id,'investigation-board')
    assert.equal(v.calls.length,0)
  }finally{v.close()}
})

test('a repeated task ID with a new investigation revision still reveals the existing task',async()=>{
  const v=await mount()
  try {
    const details={open:false};v.state.investigationDetails=details
    v.store.selectedInvestigationId='same-task';v.store.investigationRevision++
    await v.settle();details.open=false
    const before=v.scrolls.length
    v.store.investigationRevision++;await v.settle()
    assert.equal(details.open,true);assert.equal(v.scrolls.length,before+1)
  }finally{v.close()}
})

test('a project switch between ticks discards the pending reveal',async()=>{
  const v=await mount()
  try {
    const details={open:false};v.state.investigationDetails=details
    v.store.selectedInvestigationId='old-project-task';v.store.investigationRevision++
    const switchProject=nextTick(()=>{v.store.workspaceRevision++;v.store.switchingProject=true})
    await switchProject;await v.settle()
    assert.equal(details.open,false);assert.equal(v.scrolls.length,0)
  }finally{v.close()}
})

test('an added referenced task refreshes the investigation list without overwriting its dirty draft',async()=>{
  const boardSource=await readFile(project+'/frontend/src/components/InvestigationBoard.vue','utf8')
  const {descriptor}=compiler.parse(boardSource)
  let result={scope_id:'scope-a',revision:1,persisted_project:false,items:[
    {id:'task-old',draft:{title:'已有任务',hypothesis:'旧假设',supporting_notes:''}},
  ]}
  const store=reactive({ready:true,chatRunning:false,loading:false,workspaceRevision:1,
    status:{analysis_revision:1,results_revision:1},investigationRevision:0,selectedInvestigationId:'task-old'})
  globalThis.__q17BoardStore=store
  globalThis.__q17BoardApi={investigations:async()=>structuredClone(result)}
  const fixture='data:text/javascript,'+encodeURIComponent(
    'export const api=globalThis.__q17BoardApi;export const useSessionStore=()=>globalThis.__q17BoardStore')
  const code=compiler.compileScript(descriptor,{id:'q17-board-setup'}).content
    .replace(/from ['"]([^'"]+)['"]/g,(_m,dependency)=>'from '+JSON.stringify(dependency==='vue'?vueURL:fixture))
  const {default:Component}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'))
  Component.render=()=>null
  const app=renderer.createApp(Component),vm=app.mount({children:[]}),state=vm.$.setupState
  try{
    await settle()
    state.draft.title='正在编辑、尚未保存的标题'
    state.draft.supporting_notes='待核查的证据草稿'
    state.edit({target:{name:'supporting_notes'}})
    result={...result,revision:2,items:[...result.items,
      {id:'task-new',draft:{title:'刚从报告加入的任务',hypothesis:'新假设',supporting_notes:''}}]}
    store.selectedInvestigationId='task-new';store.investigationRevision++
    await settle()
    assert.equal(state.board.items.length,2)
    assert.equal(state.selected,'task-old')
    assert.equal(state.draft.title,'正在编辑、尚未保存的标题')
    assert.equal(state.draft.supporting_notes,'待核查的证据草稿')
    assert.equal(state.dirty,true)
    assert.match(state.message,/草稿保留/)
  }finally{app.unmount();delete globalThis.__q17BoardStore;delete globalThis.__q17BoardApi}
})

function selectedContext(kind='sample') {
  return {id:'e'.repeat(32),kind,title:'所选来源',summary:'来自服务端的观测',notice:'说明',
    scope:{project_id:'a',analysis_revision:1,results_revision:0,data_version:'f'.repeat(64)},
    selection:{sample_id:0},source_url:'/charts'}
}
test('明确发送才携带所选context_id，公卡随用户消息显示',async()=>{
  const v=await mount()
  try{v.store.questionContext=selectedContext();v.state.input='解释这个点';await v.state.send()
    assert.equal(v.calls.length,1);assert.equal(v.calls[0].context_id,selectedContext().id)
    assert.equal(v.store.chatMessages[0].question_context.id,selectedContext().id)
  }finally{v.close()}
})
test('上下文409保留原问题，撤掉来源且不静默无引用重试',async()=>{
  const v=await mount()
  try{v.store.questionContext=selectedContext();v.state.input='不要丢掉我的问题'
    let calls=0;globalThis.fetch=async()=>{calls++;return {ok:false,status:409,json:async()=>({detail:'上下文已过期'})}}
    await v.state.send()
    assert.equal(v.state.input,'不要丢掉我的问题');assert.equal(v.store.questionContext,null)
    assert.match(v.store.questionContextNotice,/重新选择/)
    await v.state.retryLast();assert.equal(calls,1);assert.match(v.store.questionContextNotice,/核对选择/)
  }finally{v.close()}
})
test('发送前发现客户端版本过期时不追加消息或发请求',async()=>{
  const v=await mount()
  try{v.store.questionContext=selectedContext();v.store.status.analysis_revision=2;v.state.input='保留问题'
    await v.state.send();assert.equal(v.calls.length,0);assert.equal(v.store.chatMessages.length,0)
    assert.equal(v.state.input,'保留问题')
  }finally{v.close()}
})
test('移除或更换引用后，上次请求重试必须让用户重新核对',async()=>{
  const v=await mount()
  try{v.store.questionContext=selectedContext();v.state.input='最初问题';await v.state.send()
    v.store.questionContext={...selectedContext(),id:'c'.repeat(32)}
    await v.state.retryLast();assert.equal(v.calls.length,1);assert.equal(v.state.input,'最初问题')
  }finally{v.close()}
})
test('没有监测表时仍可明确询问已选资料片段',async()=>{
  const v=await mount()
  try{v.store.status.has_data=false;v.store.questionContext=selectedContext('rag_excerpt')
    v.state.input='解释这段原文';await v.state.send()
    assert.equal(v.calls.length,1);assert.equal(v.calls[0].context_id,selectedContext().id)
  }finally{v.close()}
})

test('工作台状态串起数据、当前问题、分析进度和下一步',async()=>{
  const v=await mount()
  try{
    v.store.status={has_data:false,rows:0,has_detection:false,black_spot_count:0}
    await v.settle()
    assert.equal(v.state.workbench.dataText,'未载入数据')
    assert.equal(v.state.workbench.progressText,'尚未分析')
    assert.equal(v.state.workbench.evidenceText,'等待数据')
    v.store.status={has_data:true,rows:1801,has_detection:true,black_spot_count:0,data_identity:{kind:'synthetic'}}
    v.state.input='待发送的问题'
    await v.settle()
    assert.equal(v.state.workbench.dataText,'1801 条记录 · 仿真')
    assert.match(v.state.workbench.questionText,/待发送的问题/)
    assert.equal(v.state.workbench.progressText,'已完成基础筛查')
    assert.equal(v.state.workbench.evidenceText,'0 个预警点')
    assert.equal(v.state.workbench.nextText,'核对证据并安排复测')
  }finally{v.close()}
})

test('服务失败后保留原问题、引用和重试入口，不把输入清空',async()=>{
  const v=await mount()
  try{v.store.questionContext=selectedContext('image_asset');v.state.input='服务失败后仍要保留的问题'
    const bodies=[]
    globalThis.fetch=async(_url,options)=>{bodies.push(JSON.parse(options.body));return {ok:false,status:500,json:async()=>({detail:'模型服务暂时不可用'})}}
    await v.state.send()
    assert.equal(v.state.input,'服务失败后仍要保留的问题')
    assert.equal(v.store.questionContext?.id,selectedContext('image_asset').id)
    assert.match(v.store.chatMessages.at(-1).content,/模型服务暂时不可用/)
    await v.state.retryLast()
    assert.equal(bodies.length,2)
    assert.equal(bodies.at(-1).context_id,selectedContext('image_asset').id)
  }finally{v.close()}
})

test('会话忙碌409保留仍有效的引用和问题，不冒充来源已过期',async()=>{
  const v=await mount()
  try{v.store.questionContext=selectedContext();v.state.input='等待任务结束后询问'
    globalThis.fetch=async()=>({ok:false,status:409,headers:{get:key=>key==='X-Session-Conflict'?'busy':null},json:async()=>({detail:'当前会话正在处理任务，请稍后重试'})})
    await v.state.send()
    assert.equal(v.store.questionContext?.id,selectedContext().id)
    assert.equal(v.state.input,'等待任务结束后询问')
  }finally{v.close()}
})


test('对话顶部默认收起分析信息，展开/收起不丢草稿和引用',async()=>{
  const v=await mount()
  try{
    assert.equal(v.state.analysisExpanded,false)
    v.state.input='保留的问题草稿'
    v.store.questionContext=selectedContext('image_asset')
    await v.state.toggleAnalysis()
    assert.equal(v.state.analysisExpanded,true)
    assert.equal(v.state.input,'保留的问题草稿')
    assert.equal(v.store.questionContext.id,selectedContext('image_asset').id)
    await v.state.toggleAnalysis()
    assert.equal(v.state.analysisExpanded,false)
    assert.equal(v.state.input,'保留的问题草稿')
  }finally{v.close()}
})

test('粘贴图片会阻止默认粘贴并走真实上传，普通文字粘贴不受影响',async()=>{
  const v=await mount()
  try{
    const file={type:'image/png',name:'截图.png'}
    const imageEvent={prevented:0,preventDefault(){this.prevented++},clipboardData:{items:[{kind:'file',type:'image/png',getAsFile:()=>file}]}}
    await v.state.onPaste(imageEvent)
    assert.equal(imageEvent.prevented,1)
    assert.deepEqual(v.store.attached,file)
    const textEvent={prevented:0,preventDefault(){this.prevented++},clipboardData:{items:[{kind:'string',type:'text/plain',getAsFile:()=>null}]}}
    await v.state.onPaste(textEvent)
    assert.equal(textEvent.prevented,0)
  }finally{v.close()}
})


test('空会话没有监测表和引用时仍可直接发送普通概念问题',async()=>{
  const v=await mount()
  try{
    v.store.status.has_data=false
    v.store.questionContext=null
    v.state.input='什么是溶解氧？'
    await v.state.send()
    assert.equal(v.calls.length,1)
    assert.deepEqual(v.calls[0],{message:'什么是溶解氧？',auto:false})
    assert.equal(v.store.chatMessages[0].content,'什么是溶解氧？')
  }finally{v.close()}
})


test('未配置模型Key时保留问题并提示离线能力，不发送请求',async()=>{
  const v=await mount()
  try{
    v.store.config={target_water_class:'V',keys:{deepseek:false}}
    v.state.input='解释一下溶解氧'
    await v.state.send()
    assert.equal(v.calls.length,0)
    assert.equal(v.state.input,'解释一下溶解氧')
    assert.match(v.state.aiNotice,/未配置 DeepSeek Key/)
    assert.match(v.state.aiNotice,/示例|基础筛查/)
  }finally{v.close()}
})
