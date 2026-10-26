import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {parse,compileScript,compileTemplate} from '@vue/compiler-sfc'
import ts from 'typescript'
import {createRenderer,reactive,nextTick} from 'vue'

const vueURL=import.meta.resolve('vue')
const {descriptor}=parse(await readFile(new URL('../src/components/AnalysisCards.vue',import.meta.url),'utf8'))
const compiled=compileScript(descriptor,{id:'analysis-cards-review'})
const scriptCode=ts.transpileModule(compiled.content,{compilerOptions:{target:ts.ScriptTarget.ES2020,module:ts.ModuleKind.ESNext}}).outputText
const templateCode=compileTemplate({source:descriptor.template.content,filename:'AnalysisCards.vue',id:'analysis-cards-review',compilerOptions:{bindingMetadata:compiled.bindings}}).code

const renderer=createRenderer({
  createElement:tag=>({tag,children:[]}),
  createText:text=>({text}),
  createComment:text=>({comment:text}),
  insert(node,parent,anchor){
    if(node.parent){const old=node.parent.children.indexOf(node);if(old>=0)node.parent.children.splice(old,1)}
    node.parent=parent
    const at=anchor?parent.children.indexOf(anchor):-1
    if(at>=0)parent.children.splice(at,0,node);else parent.children.push(node)
  },
  remove(node){if(node.parent){const at=node.parent.children.indexOf(node);if(at>=0)node.parent.children.splice(at,1);node.parent=null}},
  setText(node,text){node.text=text},
  setElementText(node,text){node.children=[{text}]},
  parentNode:node=>node.parent,
  nextSibling:node=>node.parent?.children[node.parent.children.indexOf(node)+1]??null,
  patchProp(node,key,_previous,value){node.props??={};node.props[key]=value},
})

const settle=async()=>{await nextTick();await new Promise(r=>setImmediate(r));await nextTick()}
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {resolve,promise}}

const EXISTING_DRAFT={
  title:'复核氨氮异常',
  hypothesis:'上游排放或底泥释放导致氨氮偏高',
  supporting_notes:'下游复测点位与历史高值吻合',
  opposing_notes:'',
  status:'open',
  retest:{location:'断面 A 下游 50 米',latitude:30.123456,longitude:120.654321,indicators:['ammonia','ph'],plan:'连续三日早晚各采样一次',observed_at:'',result:''},
  review:{outcome:'',reviewer:'',notes:''},
}
function makeDraft(over={}){
  const base=structuredClone(EXISTING_DRAFT)
  return {...base,...over,retest:{...base.retest,...(over.retest??{})},review:{...base.review,...(over.review??{})}}
}
function makeRef(kind='fact',over={}){
  return {kind,label:'引用标签',excerpt:'证据摘要',source_url:null,notice:'',...over}
}
function makeCard(over={}){
  return {
    id:'card-1',
    title:'氨氮异常',
    finding:'本次运行发现氨氮高于历史基线。',
    support:[makeRef('fact',{fact_id:'fact-1'})],
    oppose:[],
    gaps:[],
    opposing_evidence_status:'not_provided',
    suggestion:{title:'建议复测',hypothesis:'复测确认是否持续偏高',supporting_notes:'',opposing_notes:'',retest:{location:'断面 A',latitude:null,longitude:null,indicators:['ammonia'],plan:'复测计划'}},
    ...over,
  }
}
function response(status='ready',over={}){
  const hasCards=Object.prototype.hasOwnProperty.call(over,'cards')
  const cards=hasCards?over.cards:(status==='ready'?[makeCard()]:[])
  const {cards:_ignored,...rest}=over
  return {version:'analysis-cards-v1',status,notice:'',cards,...rest}
}
function previewResponse(over={}){
  return {preview_id:'preview-1',bundle_id:'bundle-1',card_id:'card-1',scope_id:'scope-1',expected_revision:1,draft:makeDraft(),notice:'',...over}
}
function collect(node,tags=[],texts=[]){
  if(Array.isArray(node)){for(const item of node)collect(item,tags,texts);return {tags,texts}}
  if(!node)return {tags,texts}
  if(node.tag){tags.push(node.tag);collect(node.children??[],tags,texts)}
  else if(typeof node.text==='string')texts.push(node.text)
  else if(node.children)collect(node.children,tags,texts)
  return {tags,texts}
}

let serial=0
async function mount({api:apiOverrides={},store:storeOverrides={},withTemplate=false}={}){
  const calls={analysisCards:[],preview:[],confirm:[],evidenceRows:[],evidenceTool:[],route:[]}
  const api={
    analysisCards:async bundle=>{calls.analysisCards.push(bundle);return response('ready')},
    previewAnalysisCard:async (bundle,card)=>{calls.preview.push([bundle,card]);return previewResponse()},
    evidenceRows:async (...args)=>{calls.evidenceRows.push(args);return {rows:[{id:'row-1',value:'late evidence'}]}},
    evidenceTool:async (...args)=>{calls.evidenceTool.push(args);return {tool:'tool-1',output:'tool output'}},
    createQuestionContext:async ctx=>({kind:'video_frame',frame_id:ctx?.frame_id}),
    ...apiOverrides,
  }
  const store=reactive({
    ready:true,
    loading:false,
    chatRunning:false,
    switchingProject:false,
    workspaceRevision:1,
    projectState:{current:{id:'project-1'}},
    status:{analysis_revision:1,results_revision:1},
    selectedInvestigationId:'',
    async confirmAnalysisCard(bundle,card,payload){calls.confirm.push({bundle,card,payload});return {task_id:'task-1',persisted_project:true,notice:'已保存'}},
    ...storeOverrides,
  })
  const router={push:async url=>{calls.route.push(url)}}
  globalThis.__analysisCardsReview={api,store,router,questionContextLocation:ctx=>({path:'/video-frame',query:{frame:String(ctx?.frame_id??'')}})}
  const fixture='data:text/javascript,'+encodeURIComponent('export const api=globalThis.__analysisCardsReview.api;export const useSessionStore=()=>globalThis.__analysisCardsReview.store;export const useRouter=()=>globalThis.__analysisCardsReview.router;export const questionContextLocation=(ctx)=>globalThis.__analysisCardsReview.questionContextLocation(ctx);export default {render:()=>null}')+'#'+(++serial)
  const depMap={'vue':vueURL,'vue-router':fixture,'../api/client':fixture,'../stores/session':fixture,'../utils/questionContext':fixture}
  const rewrite=code=>code.replace(/from ['"]([^'"]+)['"]/g,(_m,dep)=>'from '+JSON.stringify(depMap[dep]??dep))
  const {default:Component}=await import('data:text/javascript;base64,'+Buffer.from(rewrite(scriptCode)).toString('base64'))
  if(withTemplate){
    const renderCode=templateCode.replace(/from ['"]([^'"]+)['"]/g,(_m,dep)=>'from '+JSON.stringify(dep==='vue'?vueURL:dep))
    const {render}=await import('data:text/javascript;base64,'+Buffer.from(renderCode).toString('base64'))
    Component.render=render
  }else Component.render=()=>null
  const app=renderer.createApp(Component,{bundleId:'bundle-1'})
  const container={children:[]}
  const vm=app.mount(container)
  await settle()
  return {calls,api,store,router,container,state:vm.$.setupState,async settle(){await settle()},close(){app.unmount();delete globalThis.__analysisCardsReview}}
}
async function openAndLoad(v){v.state.toggle();await v.settle()}

test('研判卡片展开后才按需 GET，收起不请求',async()=>{
  const v=await mount()
  try{
    assert.equal(v.calls.analysisCards.length,0)
    v.state.toggle()
    assert.equal(v.state.open,true)
    await v.settle()
    assert.deepEqual(v.calls.analysisCards,['bundle-1'])
    assert.equal(v.state.panelState,'ready')
    assert.equal(v.state.cards.length,1)
  }finally{v.close()}
})

test('过期卡片显示提示且不当作可用卡片',async()=>{
  const v=await mount({api:{analysisCards:async()=>response('stale',{notice:'卡片已过期'})}})
  try{
    await openAndLoad(v)
    assert.equal(v.state.panelState,'stale')
    assert.match(v.state.panelMessage,/卡片已过期/)
    assert.equal(v.state.cards.length,0)
  }finally{v.close()}
})

test('无效卡片隐藏内容并显示校验提示',async()=>{
  const v=await mount({api:{analysisCards:async()=>response('invalid',{notice:'卡片未通过校验'})}})
  try{
    await openAndLoad(v)
    assert.equal(v.state.panelState,'invalid')
    assert.match(v.state.panelMessage,/卡片未通过校验/)
    assert.equal(v.state.cards.length,0)
  }finally{v.close()}
})

test('空卡片显示本次未生成提示',async()=>{
  const v=await mount({api:{analysisCards:async()=>response('ready',{cards:[]})}})
  try{
    await openAndLoad(v)
    assert.equal(v.state.panelState,'empty')
    assert.match(v.state.panelMessage,/未生成研判卡片/)
    assert.equal(v.state.cards.length,0)
  }finally{v.close()}
})

test('加载失败后可重试并恢复当前卡片',async()=>{
  let attempt=0
  const v=await mount({api:{analysisCards:async()=>{attempt++;if(attempt===1)throw new Error('网络中断');return response('ready')}}})
  try{
    await openAndLoad(v)
    assert.equal(v.state.panelState,'failed')
    assert.match(v.state.loadError,/网络中断/)
    await v.state.loadCards()
    await v.settle()
    assert.equal(attempt,2)
    assert.equal(v.state.panelState,'ready')
    assert.equal(v.state.cards.length,1)
  }finally{v.close()}
})

test('生成预览只调用预览接口，不写入排查板',async()=>{
  const v=await mount()
  try{
    await openAndLoad(v)
    const card=v.state.cards[0]
    await v.state.startPreview(card)
    await v.settle()
    assert.equal(v.calls.preview.length,1)
    assert.equal(v.calls.confirm.length,0)
    assert.equal(v.state.preview.cardId,'card-1')
    assert.equal(v.state.preview.previewId,'preview-1')
    assert.equal(v.state.preview.editor.title,EXISTING_DRAFT.title)
    assert.deepEqual(v.state.preview.editor.indicators,EXISTING_DRAFT.retest.indicators)
  }finally{v.close()}
})

test('取消预览不写入排查板',async()=>{
  const v=await mount()
  try{
    await openAndLoad(v)
    await v.state.startPreview(v.state.cards[0])
    await v.settle()
    v.state.cancelPreview()
    await v.settle()
    assert.equal(v.state.preview,null)
    assert.equal(v.calls.confirm.length,0)
  }finally{v.close()}
})

test('确认提交只发一次，保存中重复提交不产生第二次写入',async()=>{
  const d=deferred(),confirmCalls=[]
  const v=await mount({store:{confirmAnalysisCard:async(...args)=>{confirmCalls.push(args);return d.promise}}})
  try{
    await openAndLoad(v)
    await v.state.startPreview(v.state.cards[0])
    await v.settle()
    const first=v.state.submitConfirm()
    const second=v.state.submitConfirm()
    assert.equal(confirmCalls.length,1)
    assert.equal(v.state.preview.submitting,true)
    d.resolve({task_id:'task-1',persisted_project:true,notice:'已保存'})
    await first
    await second
    await v.settle()
    assert.equal(confirmCalls.length,1)
    assert.equal(confirmCalls[0][0],'bundle-1')
    assert.equal(confirmCalls[0][1],'card-1')
    assert.equal(confirmCalls[0][2].preview_id,'preview-1')
    assert.match(confirmCalls[0][2].request_id,/^[0-9a-f-]{36}$/i)
    assert.equal(v.state.confirmed.has('card-1'),true)
    assert.equal(v.state.preview,null)
  }finally{v.close()}
})

test('相同草稿重试沿用同一请求 UUID',async()=>{
  const confirmCalls=[]
  let attempt=0
  const v=await mount({store:{confirmAnalysisCard:async(...args)=>{confirmCalls.push(args);attempt++;if(attempt===1)throw new Error('网络失败');return {task_id:'task-1',persisted_project:true}}}})
  try{
    await openAndLoad(v)
    await v.state.startPreview(v.state.cards[0])
    await v.settle()
    await v.state.submitConfirm()
    assert.ok(v.state.preview)
    assert.match(v.state.preview.error,/保存失败/)
    const firstId=confirmCalls[0][2].request_id
    await v.state.submitConfirm()
    assert.equal(confirmCalls.length,2)
    assert.equal(confirmCalls[1][2].request_id,firstId)
  }finally{v.close()}
})

test('编辑草稿后重试生成新的请求 UUID',async()=>{
  const confirmCalls=[]
  let attempt=0
  const v=await mount({store:{confirmAnalysisCard:async(...args)=>{confirmCalls.push(args);attempt++;if(attempt===1)throw new Error('网络失败');return {task_id:'task-1',persisted_project:true}}}})
  try{
    await openAndLoad(v)
    await v.state.startPreview(v.state.cards[0])
    await v.settle()
    await v.state.submitConfirm()
    const firstId=confirmCalls[0][2].request_id
    v.state.preview.editor.title='修改后的标题'
    await v.state.submitConfirm()
    assert.equal(confirmCalls.length,2)
    assert.notEqual(confirmCalls[1][2].request_id,firstId)
    assert.equal(confirmCalls[1][2].draft.title,'修改后的标题')
  }finally{v.close()}
})

test('保存失败保留表单内容和错误，不标记已确认',async()=>{
  const v=await mount({store:{confirmAnalysisCard:async()=>{throw new Error('写入失败')}}})
  try{
    await openAndLoad(v)
    await v.state.startPreview(v.state.cards[0])
    await v.settle()
    v.state.preview.editor.plan='保留的复测计划'
    await v.state.submitConfirm()
    await v.settle()
    assert.ok(v.state.preview)
    assert.equal(v.state.preview.editor.plan,'保留的复测计划')
    assert.equal(v.state.preview.editor.title,EXISTING_DRAFT.title)
    assert.match(v.state.preview.error,/保存失败（未确认已写入）/)
    assert.equal(v.state.preview.submitting,false)
    assert.equal(v.state.confirmed.size,0)
  }finally{v.close()}
})

test('保存期间 store.loading 切换不会清空当前预览表单',async()=>{
  const d=deferred()
  const v=await mount({store:{async confirmAnalysisCard(){this.loading=true;await d.promise;this.loading=false;return {task_id:'task-1',persisted_project:true}}}})
  try{
    await openAndLoad(v)
    await v.state.startPreview(v.state.cards[0])
    await v.settle()
    const pending=v.state.submitConfirm()
    await nextTick()
    assert.equal(v.store.loading,true)
    assert.ok(v.state.preview)
    assert.equal(v.state.preview.editor.title,EXISTING_DRAFT.title)
    d.resolve()
    await pending
    await v.settle()
    assert.equal(v.store.loading,false)
    assert.equal(v.state.preview,null)
  }finally{v.close()}
})

test('预览请求期间项目变化，迟到预览不覆盖当前状态',async()=>{
  const d=deferred()
  const v=await mount({api:{previewAnalysisCard:()=>d.promise}})
  try{
    await openAndLoad(v)
    const pending=v.state.startPreview(v.state.cards[0])
    await nextTick()
    v.store.projectState.current={id:'project-2'}
    d.resolve(previewResponse())
    await pending
    await v.settle()
    assert.equal(v.state.preview,null)
    assert.match(v.state.staleNotice,/分析状态已变化/)
    assert.equal(v.calls.confirm.length,0)
  }finally{v.close()}
})

test('确认请求期间项目变化，迟到结果不覆盖新选择也不标记确认',async()=>{
  const d=deferred()
  const v=await mount({store:{confirmAnalysisCard:async()=>{await d.promise;return {task_id:'task-1',persisted_project:true}}}})
  try{
    await openAndLoad(v)
    await v.state.startPreview(v.state.cards[0])
    await v.settle()
    v.store.selectedInvestigationId='existing-selection'
    const pending=v.state.submitConfirm()
    await nextTick()
    v.store.projectState.current={id:'project-2'}
    v.store.selectedInvestigationId='new-selection'
    d.resolve()
    await pending
    await v.settle()
    assert.equal(v.store.selectedInvestigationId,'new-selection')
    assert.equal(v.state.confirmed.size,0)
    assert.equal(v.state.preview,null)
  }finally{v.close()}
})

test('加载请求期间项目变化，迟到响应不覆盖新响应',async()=>{
  const d=deferred()
  let call=0
  const v=await mount({api:{analysisCards:()=>{call++;return call===1?d.promise:Promise.resolve(response('ready',{cards:[makeCard({id:'new-card'})]}))}}})
  try{
    v.state.toggle()
    await nextTick()
    assert.equal(v.state.loading,true)
    v.state.open=false
    v.store.projectState.current={id:'project-2'}
    d.resolve(response('ready',{cards:[makeCard({id:'old-card'})]}))
    await v.settle()
    assert.equal(v.state.response,null)
    assert.equal(v.state.cards.length,0)
    assert.match(v.state.staleNotice,/分析状态已更新|已变化/)
  }finally{v.close()}
})

test('内联证据请求期间分析状态变化，迟到输出不展开',async()=>{
  const d=deferred()
  const fact=makeRef('fact',{fact_id:'fact-1'})
  const v=await mount({api:{analysisCards:async()=>response('ready',{cards:[makeCard({support:[fact]})]}),evidenceRows:()=>d.promise}})
  try{
    await openAndLoad(v)
    const inline=v.state.ensureInline(fact)
    const pending=v.state.toggleInline(fact)
    await nextTick()
    assert.equal(inline.loading,true)
    v.store.workspaceRevision=2
    d.resolve({rows:[{value:'late'}]})
    await pending
    await v.settle()
    assert.equal(inline.open,false)
    assert.match(inline.error,/分析状态已变化/)
    assert.equal(inline.text,'')
  }finally{v.close()}
})

test('证据摘要中的 HTML 作为纯文本节点渲染',async()=>{
  const excerpt='<img src=x onerror="alert(1)"><script>bad()</script>'
  const v=await mount({withTemplate:true,api:{analysisCards:async()=>response('ready',{cards:[makeCard({support:[makeRef('fact',{fact_id:'fact-1',label:'<b>标签</b>',excerpt})]})]})}})
  try{
    await openAndLoad(v)
    const {tags,texts}=collect(v.container)
    assert.ok(texts.some(text=>text.includes(excerpt)))
    assert.equal(tags.includes('img'),false)
    assert.equal(tags.includes('script'),false)
    assert.equal(tags.includes('b'),false)
  }finally{v.close()}
})

test('记录链接只接受当前 bundle 且 row 合法的内部路由',async()=>{
  const v=await mount()
  try{
    const good='/charts?evidence=bundle-1&row=2#trace-map'
    assert.equal(v.state.recordRoute({kind:'record',position:2,source_url:good}),good)
    assert.equal(v.state.recordRoute({kind:'record',position:3,source_url:good}),null)
    assert.equal(v.state.recordRoute({kind:'record',position:2,source_url:'/charts?evidence=other&row=2#trace-map'}),null)
    assert.equal(v.state.recordRoute({kind:'record',position:2,source_url:'/charts?evidence=bundle-1&row=x#trace-map'}),null)
    assert.equal(v.state.recordRoute({kind:'record',position:2,source_url:'https://evil.example/charts?evidence=bundle-1&row=2#trace-map'}),null)
  }finally{v.close()}
})

test('复测指标只接受合法且不重复的选项',async()=>{
  const v=await mount()
  try{
    const stateFor=indicators=>({editor:{title:'任务',hypothesis:'假设',supportingNotes:'',opposingNotes:'',location:'',indicators,plan:''}})
    assert.equal(v.state.validate(stateFor(['ammonia','ph'])),'')
    assert.match(v.state.validate(stateFor(['ammonia','ammonia'])),/有效且不重复/)
    assert.match(v.state.validate(stateFor(['ammonia','bogus'])),/有效且不重复/)
    assert.match(v.state.validate(stateFor(['toString'])),/有效且不重复/)
    assert.equal(v.state.validate(stateFor(['ammonia','do','cod','ph','turbidity','sd','orp','flow','connection'])),'')
  }finally{v.close()}
})


test('重新读取已保存卡片可恢复确认标记，不能再次生成新草稿',async()=>{
  const v=await mount({api:{analysisCards:async()=>response('ready',{cards:[makeCard({confirmed_task_id:'saved-task'})]})}})
  try{await openAndLoad(v);assert.equal(v.state.confirmed.has('card-1'),true)
    await v.state.startPreview(v.state.cards[0]);assert.equal(v.calls.preview.length,0)
  }finally{v.close()}
})

test('同项目分析版本变化时保留编辑草稿，禁止保存，取消后可重新加载',async()=>{
  const v=await mount()
  try{await openAndLoad(v);await v.state.startPreview(v.state.cards[0]);v.state.preview.editor.plan='尚未保存的编辑'
    v.store.status.results_revision++;await v.settle()
    assert.equal(v.state.preview.editor.plan,'尚未保存的编辑');assert.equal(v.state.preview.blocked,true)
    await v.state.submitConfirm();assert.equal(v.calls.confirm.length,0)
    v.state.cancelPreview();await v.settle();assert.equal(v.state.preview,null);assert.equal(v.state.panelState,'ready')
  }finally{v.close()}
})


test('视频证据跳转使用已绑定项目和数据版本的帧上下文接口',async()=>{
  let body
  const v=await mount({api:{createQuestionContext:async value=>{body=value;return {kind:'video_frame',frame_id:value.frame_id}}}})
  try{await v.state.openVideoFrame(makeRef('video_frame',{frame_id:0,video_revision:'a'.repeat(64)}))
    assert.deepEqual(body,{kind:'video_frame',frame_id:0,video_revision:'a'.repeat(64),project_id:'project-1',analysis_revision:1,results_revision:1})
    assert.equal(v.calls.route.length,1)
  }finally{v.close()}
})


test('首次查看证据行在返回后真实模板退出加载状态并显示数据',async()=>{
  const d=deferred(),fact=makeRef('fact',{fact_id:'fact-1'})
  const v=await mount({withTemplate:true,api:{evidenceRows:()=>d.promise}})
  try{await openAndLoad(v)
    const pending=v.state.toggleInline(fact);await v.settle()
    assert.ok(collect(v.container).texts.some(text=>text.includes('加载中')))
    d.resolve({rows:[{value:'首次返回的原始观测'}]});await pending;await v.settle()
    const texts=collect(v.container).texts
    assert.ok(texts.some(text=>text.includes('首次返回的原始观测')))
    assert.equal(texts.some(text=>text.includes('加载中')),false)
  }finally{d.resolve();v.close()}
})
