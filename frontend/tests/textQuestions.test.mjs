import test from 'node:test'
import assert from 'node:assert/strict'
import {createServer} from 'vite'
import {createPinia} from 'pinia'
import {selectedPassage,quoteKey} from '../src/utils/textQuestions.js'
const memory=new Map()
globalThis.localStorage={getItem:k=>memory.get(k)||null,setItem:(k,v)=>memory.set(k,v),removeItem:k=>memory.delete(k)}
const server=await createServer({configLoader:'runner',server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,entries:[]},appType:'custom',logLevel:'error',mode:'test'})
const {useSessionStore}=await server.ssrLoadModule('/src/stores/session.js')
test.after(()=>server.close())
const selection={text:'DO=0不等于缺测',source:{kind:'help',help_id:'screening_help'},title:'页面说明'}
const scope={project_id:'a',analysis_revision:1,results_revision:2,data_version:'d'.repeat(64)}
const card={id:'a'.repeat(32),kind:'text_quote',title:'所选文字',summary:'出处已核对，不代表内容正确',notice:'仅作待解释材料',scope,selection:{text:selection.text,source:selection.source},source_url:'/tech',excerpt:selection.text}
const reply=body=>new Response(JSON.stringify(body),{headers:{'Content-Type':'application/json'}})
function state(){const s=useSessionStore(createPinia());s.ready=true;s.status={has_data:true,analysis_revision:1,results_revision:2};s.projectState.current={id:'a'};return s}
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve}}

test('划词只读取同一个可见的显式来源区域，拒绝表单、隐藏与跨来源选择',()=>{
  const root={dataset:{quoteSource:JSON.stringify(selection.source),quoteTitle:'来源'},closest:()=>null,getClientRects:()=>[{}]}
  const node={nodeType:1,closest:()=>root}
  const range={isCollapsed:false,rangeCount:1,anchorNode:node,focusNode:node,toString:()=>selection.text}
  assert.equal(selectedPassage(range).text,selection.text)
  assert.equal(selectedPassage({...range,isCollapsed:true}),null)
  assert.equal(selectedPassage({...range,focusNode:{nodeType:1,closest:()=>({...root})}}),null)
  root.getClientRects=()=>[];assert.equal(selectedPassage(range),null)
  root.getClientRects=()=>[{}];root.closest=()=>({});assert.equal(selectedPassage(range),null)
})
test('添加引用保留主草稿、不发请求、重复添加不产生第二份引用',()=>{
  const s=state();s.chatDraft='已经写了一半';let called=0;globalThis.fetch=async()=>{called++;return reply(card)}
  s.captureTextQuote(selection,'main');s.captureTextQuote(selection,'main')
  assert.equal(s.chatDraft,'已经写了一半');assert.equal(s.textQuestions.pendingMain.text,selection.text);assert.equal(called,0)
})
test('主分析运行中可以整理侧边引用与草稿，但不能发请求',async()=>{
  const s=state();s.chatRunning=true;s.captureTextQuote(selection,'side')
  assert.equal(s.textQuestions.open,true);assert.equal(s.textQuestions.pendingSide.text,selection.text)
  await assert.rejects(s.prepareSideThread(),/尚未完成/)
})
test('超长引用不截断，原有引用和主草稿保持',()=>{
  const s=state();s.captureTextQuote(selection,'main');const before=quoteKey(s.textQuestions.pendingMain)
  s.captureTextQuote({...selection,text:'长'.repeat(3001)},'main')
  assert.match(s.textQuestions.error,/3000/);assert.equal(quoteKey(s.textQuestions.pendingMain),before)
})
test('显式发送准备仅上传所选文字与来源，成功后变成核对卡',async()=>{
  const s=state(),calls=[];s.captureTextQuote(selection,'main')
  globalThis.fetch=async(url,options)=>{calls.push(JSON.parse(options.body));return reply(card)}
  await s.preparePendingQuote();assert.equal(calls.length,1);assert.equal(calls[0].text,selection.text)
  assert.equal(calls[0].source.help_id,'screening_help');assert.equal(s.questionContext.id,card.id);assert.equal(s.textQuestions.pendingMain,null)
})
test('切换项目后的迟到引用不会覆盖新工作区或主草稿',async()=>{
  const s=state(),d=deferred();s.captureTextQuote(selection,'main');globalThis.fetch=()=>d.promise
  const pending=s.preparePendingQuote();s.workspaceRevision++;s.chatDraft='新问题';d.resolve(reply(card))
  assert.equal(await pending,null);assert.equal(s.questionContext,null);assert.equal(s.chatDraft,'新问题')
})
test('关闭重开侧边面板保留独立草稿，创建线程不挤掉主草稿',async()=>{
  const s=state();s.chatDraft='主问题';s.captureTextQuote(selection,'side')
  s.textQuestions.drafts[quoteKey(s.textQuestions.pendingSide)]='侧边问题';s.textQuestions.open=false;s.textQuestions.open=true
  globalThis.fetch=async url=>reply(url.endsWith('/context')?card:{id:'b'.repeat(32),quote:card,messages:[],requests:[],updated_at:'now'})
  const thread=await s.prepareSideThread();assert.equal(s.textQuestions.drafts[thread.id],'侧边问题');assert.equal(s.chatDraft,'主问题')
})
test('侧边任务占用全局操作门禁而不替换主Agent任务',()=>{
  const s=state();s.sideRunning=true;s.adoptAgentRun({run_id:'main',status:'succeeded',busy:false})
  assert.equal(s.chatRunning,true);assert.equal(s.agentRun.run_id,'main');assert.equal(s.beginAgentStream(),false)
})
test('历史null项目身份转入主对话时原样保留，不能偷换为当前项目',()=>{
  const s=state();s.captureTextQuote(selection,'main',{...scope,project_id:null})
  assert.equal(s.textQuestions.pendingMain.project_id,null)
})

const thread=(id,text=selection.text)=>({id,quote:{...card,excerpt:text,selection:{text,source:selection.source}},messages:[],requests:[],updated_at:'now'})
test('重新选择B引用时，不能仍激活后来手动打开的A线程',()=>{
  const s=state(),b=thread('b'.repeat(32)),a=thread('a'.repeat(32),'另一段')
  s.textQuestions.threads=[a,b];s.captureTextQuote(selection,'side')
  s.textQuestions.active=a.id
  s.captureTextQuote(selection,'side')
  assert.equal(s.textQuestions.active,b.id)
})
test('服务器复用已有线程时，准备引用不能覆盖该线程尚未发送的草稿',async()=>{
  const s=state(),b=thread('b'.repeat(32));s.textQuestions.threads=[b]
  s.captureTextQuote(selection,'side');s.textQuestions.active=''
  s.textQuestions.drafts[b.id]='已有但未发送的问题'
  globalThis.fetch=async url=>reply(url.endsWith('/context')?card:b)
  await s.prepareSideThread()
  assert.equal(s.textQuestions.drafts[b.id],'已有但未发送的问题')
})
test('引用准备期间切换历史线程，迟到响应不抢回用户当前的选择',async()=>{
  const s=state(),a=thread('a'.repeat(32),'另一段'),b=thread('b'.repeat(32)),d=deferred()
  s.textQuestions.threads=[a];s.captureTextQuote(selection,'side')
  globalThis.fetch=()=>d.promise
  const pending=s.prepareSideThread();s.textQuestions.active=a.id
  d.resolve(reply(card));assert.equal(await pending,null)
  assert.equal(s.textQuestions.active,a.id)
})
test('当前选中的线程丢失时明确报错，不能点击发送后无反馈',async()=>{
  const s=state();s.captureTextQuote(selection,'side');s.textQuestions.active='missing'
  await assert.rejects(s.prepareSideThread(),/线程.*不存在|重新选择/)
})
