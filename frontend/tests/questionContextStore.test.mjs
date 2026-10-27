import test from 'node:test'
import assert from 'node:assert/strict'
import {createServer} from 'vite'
import {createPinia} from 'pinia'
import {isQuestionContext,questionContextLocation,questionContextMatches} from '../src/utils/questionContext.js'
const memory=new Map()
globalThis.localStorage={getItem:k=>memory.get(k)||null,setItem:(k,v)=>memory.set(k,v),removeItem:k=>memory.delete(k)}
const server=await createServer({configLoader:'runner',server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,entries:[]},appType:'custom',logLevel:'error',mode:'test'})
const {useSessionStore}=await server.ssrLoadModule('/src/stores/session.js')
test.after(()=>server.close())
const reply=(body,status=200)=>new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json','X-Session-Id':'context-test'}})
function card(extra={}) {return {id:'a'.repeat(32),kind:'sample',title:'采样点 #3',summary:'DO=0，原始观测',notice:'需要核对依据',scope:{project_id:'project-a',analysis_revision:2,results_revision:3,data_version:'d'.repeat(64)},selection:{sample_id:2},source_url:'/charts',...extra}}
function state(){const v=useSessionStore(createPinia());v.ready=true;v.status={has_data:true,analysis_revision:2,results_revision:3};v.projectState.current={id:'project-a'};return v}
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}

test('来源卡路由由枚举和ID重建，导入的URL不能执行',()=>{
  const c=card({source_url:'javascript:alert(1)'})
  assert.equal(isQuestionContext(c),true)
  assert.equal(isQuestionContext(card({title:'资料'.repeat(300)+' · 章节'})),true)
  assert.equal(isQuestionContext(card({scope:{...c.scope,data_version:'unverified'}})),false)
  assert.deepEqual(questionContextLocation(c),{path:'/charts',query:{context_id:c.id},hash:'#trace-map'})
  assert.equal(questionContextLocation(card({id:'../private'})),null)
  assert.equal(isQuestionContext(card({kind:'unknown'})),false)
  const picture=card({kind:'image_asset',selection:{asset_id:'a'.repeat(32),asset_revision:'b'.repeat(64)},source_url:'/chat?context_id='+card().id+'#image-asset'})
  assert.equal(isQuestionContext(picture),true)
  assert.deepEqual(questionContextLocation(picture),{path:'/chat',query:{context_id:picture.id},hash:'#image-asset'})
  assert.equal(isQuestionContext(card({scope:{...c.scope,analysis_revision:-1}})),false)
})

test('选择采样点保留用户半句并只请求预览，不自动发送',async()=>{
  const v=state(),calls=[];v.chatDraft='已经输入的半句'
  globalThis.fetch=async(url,options)=>{calls.push({url,body:JSON.parse(options.body)});return reply(card())}
  const selected=await v.prepareQuestionContext({kind:'sample',sample_id:2})
  assert.equal(selected.id,card().id);assert.equal(v.chatDraft,'已经输入的半句')
  assert.equal(calls.length,1);assert.equal(calls[0].url,'/api/agent/context')
  assert.deepEqual(calls[0].body,{kind:'sample',sample_id:2,project_id:'project-a',analysis_revision:2,results_revision:3})
  assert.equal(v.loading,false)
})

test('空草稿得到可编辑的问题而不是自动模型请求',async()=>{
  const v=state();let calls=0
  globalThis.fetch=async()=>{calls++;return reply(card())}
  await v.prepareQuestionContext({kind:'sample',sample_id:2})
  assert.match(v.chatDraft,/采样点/);assert.equal(calls,1)
})

for(const field of ['project','workspace','analysis','results']){
  test('预览迟到时不写入变化后的'+field+'上下文',async()=>{
    const v=state(),d=deferred();v.chatDraft='保留原问题';globalThis.fetch=()=>d.promise
    const pending=v.prepareQuestionContext({kind:'sample',sample_id:2})
    if(field==='project')v.projectState.current={id:'project-b'}
    if(field==='workspace')v.workspaceRevision++
    if(field==='analysis')v.status.analysis_revision++
    if(field==='results')v.status.results_revision++
    d.resolve(reply(card()));assert.equal(await pending,null)
    assert.equal(v.questionContext,null);assert.equal(v.chatDraft,'保留原问题');assert.equal(v.error,'')
  })
}

test('过期预览的失败不覆盖新项目提示',async()=>{
  const v=state(),d=deferred();globalThis.fetch=()=>d.promise
  const pending=v.prepareQuestionContext({kind:'sample',sample_id:2})
  v.workspaceRevision++;d.reject(Error('旧项目失败'));assert.equal(await pending,null)
  assert.equal(v.error,'');assert.equal(v.questionContextNotice,'')
})

test('错误版本的服务返回不能成为当前选择',async()=>{
  const v=state();v.chatDraft='不丢草稿'
  globalThis.fetch=async()=>reply(card({scope:{...card().scope,results_revision:4}}))
  await assert.rejects(v.prepareQuestionContext({kind:'sample',sample_id:2}),/版本/)
  assert.equal(v.questionContext,null);assert.equal(v.chatDraft,'不丢草稿')
})

test('只移除同一张来源卡，保留后来新选择和文字',()=>{
  const v=state();v.questionContext=card();v.chatDraft='提问草稿'
  v.clearQuestionContextById('b'.repeat(32));assert.ok(v.questionContext)
  v.clearQuestionContextById(card().id);assert.equal(v.questionContext,null);assert.equal(v.chatDraft,'提问草稿')
})

test('旧数据版本刷新后撤销引用而不吞掉问题',async()=>{
  const v=state();v.questionContext=card();v.chatDraft='待重选的问题'
  v.refreshResults=async()=>{}
  globalThis.fetch=async()=>reply({...v.status,results_revision:4})
  await v.refreshStatus();assert.equal(v.questionContext,null)
  assert.match(v.questionContextNotice,/重新选择/);assert.equal(v.chatDraft,'待重选的问题')
})

test('切换失败保留当前引用和草稿，成功切换清空它们',async()=>{
  const v=state();v.questionContext=card();v.chatDraft='旧项目问题'
  await assert.rejects(v.changeProject(async()=>{throw Error('切换失败')}),/切换失败/)
  assert.ok(v.questionContext);assert.equal(v.chatDraft,'旧项目问题')
  v.refreshStatus=async()=>{};v.refreshPreview=async()=>{}
  globalThis.fetch=async url=>reply(url.endsWith('/config')?{keys:{},target_water_class:'V',valid_classes:[]}:{messages:[],analysis_revision:2})
  await v.adoptProject({projects:[],current:{id:'project-b'},river_ui:{},model:null,restore_error:''})
  assert.equal(v.questionContext,null);assert.equal(v.chatDraft,'')
})

test('已选RAG片段不依赖监测表，仍保持来源身份',async()=>{
  const v=state();v.status.has_data=false
  globalThis.fetch=async()=>reply(card({kind:'rag_excerpt',selection:{retrieval_id:'r',evidence_id:'ev'},excerpt:'原文片段'}))
  const result=await v.prepareQuestionContext({kind:'rag_excerpt',retrieval_id:'r',evidence_id:'ev'})
  assert.equal(result.kind,'rag_excerpt');assert.match(v.chatDraft,/资料片段/)
})

test('全局任务忙碌时不创建第二个上下文请求',async()=>{
  const v=state();v.loading=true;let calls=0;globalThis.fetch=async()=>{calls++;return reply(card())}
  await assert.rejects(v.prepareQuestionContext({kind:'sample',sample_id:2}),/当前任务/)
  assert.equal(calls,0)
})


test('没有监测表时仍可上传截图并建立图片上下文，侧边图片可转回主对话',async()=>{
  const v=state();v.status.has_data=false;v.chatDraft=''
  const asset={asset_id:'a'.repeat(32),sha256:'b'.repeat(64),media_type:'image/png',width:100,height:80,bytes:1200,source:'upload',source_name:'截图.png',created_at:'2026-10-27T00:00:00Z',preview_url:'/api/media/'+'a'.repeat(32)}
  const picture={...card(),kind:'image_asset',title:'图片附件',summary:'100×80',selection:{asset_id:asset.asset_id,asset_revision:asset.sha256},source_url:'/chat?context_id='+card().id+'#image-asset'}
  const calls=[]
  globalThis.fetch=async(url,options)=>{
    calls.push({url,body:typeof options.body==='string'?JSON.parse(options.body):options.body})
    return reply(url==='/api/media'?asset:picture)
  }
  const file={type:'image/png',name:'截图.png'}
  const selected=await v.attachMedia(file)
  assert.equal(selected.kind,'image_asset')
  assert.equal(v.questionContext.kind,'image_asset')
  assert.match(v.chatDraft,/图片/)
  assert.deepEqual(calls.map(c=>c.url),['/api/media','/api/agent/context'])
  assert.equal(calls[1].body.kind,'image_asset')
  assert.equal(calls[1].body.asset_revision,asset.sha256)
  const quote={...picture,id:'c'.repeat(32)}
  const transferred=await v.transferQuoteToMain(quote)
  assert.equal(transferred.kind,'image_asset')
  assert.equal(v.questionContext.selection.asset_id,asset.asset_id)
})


test('侧边可直接上传截图并创建图片线程，不改变主对话当前选择',async()=>{
  const v=state();v.status.has_data=false
  const main={...card(),id:'a'.repeat(32)}
  v.questionContext=main
  const asset={asset_id:'a'.repeat(32),sha256:'b'.repeat(64),media_type:'image/png',width:100,height:80,bytes:1200,source:'upload',source_name:'侧边截图.png',created_at:'2026-10-27T00:00:00Z',preview_url:'/api/media/'+'a'.repeat(32)}
  const picture={...card(),id:'c'.repeat(32),kind:'image_asset',title:'侧边截图.png',summary:'100×80',notice:'图片材料',selection:{asset_id:asset.asset_id,asset_revision:asset.sha256},source_url:'/chat?context_id='+'c'.repeat(32)+'#image-asset'}
  const thread={id:'e'.repeat(32),quote:picture,messages:[],requests:[],updated_at:'2026-10-27T00:00:00Z'}
  const calls=[]
  globalThis.fetch=async(url,options)=>{
    calls.push({url,body:typeof options.body==='string'?JSON.parse(options.body):options.body})
    if(url==='/api/media')return reply(asset)
    if(url==='/api/agent/context')return reply(picture)
    if(url==='/api/side/threads')return reply(thread)
    throw Error('unexpected '+url)
  }
  assert.equal(questionContextMatches(picture,v),true)
  assert.equal(isQuestionContext(picture),true)
  const created=await v.attachSideMedia({type:'image/png',name:'侧边截图.png'})
  assert.equal(created.id,thread.id)
  assert.equal(v.questionContext.id,main.id)
  assert.equal(v.textQuestions.active,thread.id)
  assert.match(v.textQuestions.drafts[thread.id],/图片/)
  assert.deepEqual(calls.map(c=>c.url),['/api/media','/api/agent/context','/api/side/threads'])
  assert.equal(calls[2].body.context_id,picture.id)
})
