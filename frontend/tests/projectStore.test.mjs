// 使用Vite加载实际Pinia模块；仅模拟HTTP响应，不启动或控制浏览器。
import test from 'node:test'
import assert from 'node:assert/strict'
import { createServer } from 'vite'
import { createPinia } from 'pinia'

const memory = new Map()
globalThis.localStorage = { getItem:k=>memory.get(k)||null, setItem:(k,v)=>memory.set(k,v), removeItem:k=>memory.delete(k) }
const server = await createServer({ configLoader:'runner', server:{middlewareMode:true,hmr:false,ws:false}, optimizeDeps:{noDiscovery:true,entries:[]}, appType:'custom', logLevel:'error', mode:'test' })
const { useSessionStore } = await server.ssrLoadModule('/src/stores/session.js')
test.after(async () => { await server.close() })
const reply = (body, status=200) => new Response(JSON.stringify(body),{
  status, headers:{'Content-Type':'application/json','X-Session-Id':'test-session'},
})

test('临时会话导出给出明确提示，不发送空项目ID',async()=>{
  const store=useSessionStore(createPinia())
  let requests=0
  globalThis.fetch=async()=>{requests++;throw new Error('不应请求')}
  await assert.rejects(store.exportProject(),/先保存项目/)
  assert.equal(requests,0)
  assert.equal(store.loading,false)
  assert.match(store.error,/先保存项目/)
})

test('任务忙碌时禁止数据操作，旧进度不能覆盖新请求',async()=>{
  const store=useSessionStore(createPinia());store.ready=true;store.status.analysis_revision=3
  store.adoptAgentRun({run_id:'server',busy:true,analysis_revision:3})
  await assert.rejects(store._run(()=>{throw new Error('不应运行')}),/尚未完成/)
  store.chatRunning=false;assert.equal(store.beginAgentStream(),true)
  store.agentActiveId='new'
  store.adoptAgentRun({run_id:'old',busy:false,analysis_revision:3})
  assert.equal(store.agentRun,null);assert.equal(store.chatRunning,true)
  store.adoptAgentRun({run_id:'new',busy:true,analysis_revision:3})
  assert.equal(store.agentRun.run_id,'new')
})

test('启动新请求会作废先前轮询的迟到响应',async()=>{
  const store=useSessionStore(createPinia());store.ready=true
  let release
  globalThis.fetch=()=>new Promise(resolve=>{release=()=>resolve(reply({run_id:'old',busy:false}))})
  const polling=store.refreshAgentRun()
  store.beginAgentStream();store.agentActiveId='new'
  release();await polling
  assert.equal(store.agentRun,null);assert.equal(store.chatRunning,true)
})

test('停止请求的迟到结果不能覆盖后来任务',async()=>{
  const store=useSessionStore(createPinia());store.ready=true;store.agentRun={run_id:'old',busy:true}
  let release
  globalThis.fetch=()=>new Promise(resolve=>{release=()=>resolve(reply({run_id:'old',busy:false,status:'cancelled'}))})
  const stop=store.stopAgentRun()
  store.agentRun={run_id:'new',busy:true}
  release();await stop
  assert.equal(store.agentRun.run_id,'new')
})

test('启动失败保留具体阶段，重连成功会清除错误并恢复历史和项目', async()=>{
  const store=useSessionStore(createPinia())
  let fail=true
  globalThis.fetch=async path=>{
    if(path==='/api/health')return reply({status:'ok'})
    if(path==='/api/config')return reply({keys:{}})
    if(path==='/api/data/status')return reply({has_data:false,analysis_revision:0,results_revision:0})
    if(path==='/api/analysis/results')return reply({warnings:[]})
    if(path==='/api/agent/history'){
      if(fail)return reply({detail:'历史存储不可读'},503)
      return reply({messages:[{content:'已有对话'}]})
    }
    if(path==='/api/projects')return reply({current:{id:'saved'},projects:[{id:'saved'}],river_ui:{}})
    throw new Error(path)
  }
  await store.bootstrap()
  assert.equal(store.ready,false)
  assert.equal(store.connecting,false)
  assert.match(store.error,/恢复对话.*历史存储不可读/)
  fail=false
  await store.bootstrap()
  assert.equal(store.ready,true)
  assert.equal(store.error,'')
  assert.equal(store.bootstrapStage,'已连接')
  assert.equal(store.projectState.current.id,'saved')
  assert.equal(store.chatMessages[0].content,'已有对话')
})

test('重连不并发请求且不会在分析运行时重新初始化',async()=>{
  const store=useSessionStore(createPinia())
  let calls=0, release
  globalThis.fetch=async()=>{
    calls++
    return new Promise(resolve=>{release=()=>resolve(reply({detail:'服务离线'},503))})
  }
  const pending=store.bootstrap()
  await store.bootstrap()
  assert.equal(calls,1)
  release();await pending
  store.chatRunning=true
  await store.bootstrap()
  assert.equal(calls,1)
})

test('切换请求发出时隐藏旧视图，版本相同时仍清空缓存并恢复新历史', async () => {
  const store=useSessionStore(createPinia())
  store.ready=true
  store.status={analysis_revision:2,has_data:true}
  store.projectState={current:{id:'A',name:'河流A'},river_ui:{},projects:[]}
  store.preview={rows:[{old:true}]};store.blackSpots={old:true};store.chatMessages=[{content:'A'}]
  let release
  globalThis.fetch=async path => {
    if(path==='/api/projects/B/open') return new Promise(resolve => { release=()=>resolve(reply({
      current:{id:'B',name:'河流B'},river_ui:{form:{length_m:800}},model:null,projects:[],
    })) })
    if(path==='/api/config') return reply({target_water_class:'III'})
    if(path==='/api/data/status') return reply({analysis_revision:2,has_data:false,rows:0})
    if(path==='/api/agent/history') return reply({messages:[{content:'B'}]})
    throw new Error(`Unexpected request ${path}`)
  }
  const switching=store.openProject('B')
  assert.equal(store.switchingProject,true)
  release();await switching
  assert.equal(store.switchingProject,false)
  assert.equal(store.projectState.current.id,'B')
  assert.equal(store.blackSpots,null)
  assert.deepEqual(store.preview.rows,[])
  assert.equal(store.chatMessages[0].content,'B')
  assert.equal(store.workspaceRevision,1)
  assert.equal(store.riverForm.length_m,800)
})

test('打开失败保留当前项目和输入，解除切换状态', async () => {
  const store=useSessionStore(createPinia());store.ready=true
  store.projectState={current:{id:'A'},river_ui:{},projects:[]};store.riverForm={length_m:1200}
  globalThis.fetch=async () => reply({detail:'项目冲突'},409)
  await assert.rejects(store.openProject('B'),/项目冲突/)
  assert.equal(store.projectState.current.id,'A')
  assert.equal(store.riverForm.length_m,1200)
  assert.equal(store.switchingProject,false)
  assert.equal(store.loading,false)
})

test('切换已提交但后续请求失败时停用旧视图，避免显示混合数据', async () => {
  const store=useSessionStore(createPinia());store.ready=true
  globalThis.fetch=async path => {
    if(path.endsWith('/open')) return reply({current:{id:'B'},river_ui:{},projects:[]})
    return reply({detail:'临时断线'},503)
  }
  await assert.rejects(store.openProject('B'),/临时断线/)
  assert.equal(store.ready,false)
  assert.equal(store.projectState.current.id,'B')
  assert.equal(store.switchingProject,false)
})

test('启动时恢复已保存的零预警检测与交叉印证，不重新执行分析',async()=>{
  const store=useSessionStore(createPinia()),calls=[]
  const status={analysis_revision:7,results_revision:0,has_data:true,rows:3,has_detection:true,black_spot_count:0}
  globalThis.fetch=async(path,options)=>{
    calls.push([path,options.method])
    if(path==='/api/health')return reply({ok:true})
    if(path==='/api/config')return reply({keys:{},target_water_class:'V'})
    if(path==='/api/data/status')return reply(status)
    if(path==='/api/projects')return reply({current:{id:'saved'},river_ui:{form:{length_m:900}},projects:[]})
    if(path==='/api/analysis/results')return reply({analysis_revision:7,results_revision:0,
      black_spots:{result:{'黑点总数':0}},cross_validation:{'时间对齐':{'时间偏移秒':-5}},comprehensive:null,warnings:[]})
    if(path==='/api/agent/history')return reply({messages:[{content:'历史报告'}]})
    if(path.startsWith('/api/data/preview'))return reply({rows:[{}],total:3})
    throw new Error(path)
  }
  await store.bootstrap()
  assert.equal(store.ready,true)
  assert.equal(store.blackSpots.result['黑点总数'],0)
  assert.equal(store.crossValidation['时间对齐']['时间偏移秒'],-5)
  assert.equal(store.riverForm.length_m,900)
  assert.ok(calls.every(([,method])=>method==='GET'))
})

test('预警数量相同但结果版本变化时更新结果并保留模型草稿和历史',async()=>{
  const store=useSessionStore(createPinia())
  store.status={analysis_revision:2,results_revision:1,has_data:true,black_spot_count:3}
  store.projectState={current:{id:'A'},projects:[],river_ui:{}}
  store.riverForm={length_m:800};store.chatMessages=[{content:'历史'}]
  store.blackSpots={result:{old:true}};store.crossValidation={old:true}
  globalThis.fetch=async path=>{
    if(path==='/api/data/status')return reply({...store.status,results_revision:2})
    if(path==='/api/analysis/results')return reply({analysis_revision:2,results_revision:2,
      black_spots:{result:{'黑点总数':3,'聚类热点数':2}},cross_validation:null,comprehensive:null,warnings:[]})
    throw new Error(path)
  }
  await store.refreshStatus()
  assert.equal(store.blackSpots.result['聚类热点数'],2)
  assert.equal(store.crossValidation,null)
  assert.equal(store.riverForm.length_m,800)
  assert.equal(store.chatMessages[0].content,'历史')
})

test('项目切换后到达的旧结果不能覆盖新项目',async()=>{
  const store=useSessionStore(createPinia())
  store.status={analysis_revision:2,results_revision:1}
  store.projectState={current:{id:'A'}}
  let release
  globalThis.fetch=()=>new Promise(resolve=>{release=()=>resolve(reply({analysis_revision:2,results_revision:1,
    black_spots:{result:{old:true}},cross_validation:null,warnings:[]}))})
  const old=store.refreshResults()
  store.projectState={current:{id:'B'}};store.workspaceRevision++
  store.blackSpots={result:{new:true}}
  release();await old
  assert.deepEqual(store.blackSpots,{result:{new:true}})
})

test('缓存读取失败显示可重试说明，保留其它可用功能',async()=>{
  const store=useSessionStore(createPinia());store.ready=true
  store.status={analysis_revision:2,results_revision:1}
  globalThis.fetch=async()=>reply({detail:'临时断线'},503)
  await store.refreshResults()
  assert.equal(store.ready,true)
  assert.match(store.resultsWarnings[0],/加载失败.*临时断线.*刷新重试/)
})

test('并行提交被拒绝且不提前解除首个任务的忙碌标记',async()=>{
  const store=useSessionStore(createPinia());let release,secondCalled=false
  const first=store._run(()=>new Promise(resolve=>{release=resolve}))
  await assert.rejects(store._run(()=>{secondCalled=true}),/任务尚未完成/)
  assert.equal(secondCalled,false);assert.equal(store.loading,true)
  release();await first
  assert.equal(store.loading,false)
})
