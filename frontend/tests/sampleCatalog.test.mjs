// 示例目录与载入的 Pinia 行为；模拟 HTTP，不启动浏览器或外部服务。
import test from 'node:test'
import assert from 'node:assert/strict'
import {createServer} from 'vite'
import {createPinia} from 'pinia'

const memory=new Map()
globalThis.localStorage={getItem:k=>memory.get(k)||null,setItem:(k,v)=>memory.set(k,v),removeItem:k=>memory.delete(k)}
const server=await createServer({configLoader:'runner',server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,entries:[]},appType:'custom',logLevel:'error',mode:'test'})
const {useSessionStore}=await server.ssrLoadModule('/src/stores/session.js')
test.after(()=>server.close())
const reply=(body,status=200)=>new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json','X-Session-Id':'sample-test'}})
const sample={id:'S01',title:'S01 仿真正常背景',description:'正常背景',data_kind:'synthetic',version:'v1-test',rows:1801,columns:['时间'],time_range:['2026-10-21 09:00:00','2026-10-21 11:30:00'],coordinate_system:'WGS84',source_reference:'仿真数据/S01.xlsx',has_video:false,size_bytes:100}
const projectState={projects:[{id:'p'.repeat(32),name:'仿真示例 S01',revision:1}],current:{id:'p'.repeat(32),name:'仿真示例 S01',revision:1},river_ui:{},model:null,restore_error:''}

test('示例目录读取一次并在再次打开面板时复用，不创建演示项目',async()=>{
  const store=useSessionStore(createPinia())
  let calls=0
  globalThis.fetch=async url=>{calls++;assert.equal(url,'/api/data/samples');return reply({manifest_version:1,data_kind:'synthetic',notice:'仿真',samples:[sample]})}
  const first=await store.loadSamples()
  const second=await store.loadSamples()
  assert.equal(calls,1)
  assert.equal(first.samples.length,1)
  assert.equal(second.samples.length,1)
  assert.equal(store.sampleCatalogLoaded,true)
  assert.equal(store.sampleCatalogError,'')
})

test('目录服务错误保留错误分类和重试入口，不伪装为空列表',async()=>{
  const store=useSessionStore(createPinia())
  globalThis.fetch=async()=>reply({detail:'示例清单不可用'},503)
  await assert.rejects(store.loadSamples(),/示例清单不可用/)
  assert.equal(store.sampleCatalogLoaded,false)
  assert.match(store.sampleCatalogError,/示例清单不可用/)
})

test('载入示例携带当前项目/版本/河道表单，并采用返回的独立项目状态',async()=>{
  const store=useSessionStore(createPinia())
  store.ready=true
  store.status={has_data:false,analysis_revision:0,results_revision:0}
  store.projectState.current=null
  store.riverForm={length_m:800}
  const calls=[]
  globalThis.fetch=async(url,options)=>{
    calls.push({url,body:options?.body?JSON.parse(options.body):null})
    if(url==='/api/data/samples/S01/load')return reply({ok:true,sample,report:'载入完成',rows:1801,columns:['时间'],time_range:['a','b'],data_identity:{kind:'synthetic'},video_frames:0,detection:{requested:true,ok:true,black_spots:0,clusters:0,saved:true},project_state:projectState})
    if(url==='/api/config')return reply({keys:{},target_water_class:'V',valid_classes:['V']})
    if(url==='/api/projects')return reply(projectState)
    if(url==='/api/data/status')return reply({has_data:true,rows:1801,analysis_revision:1,results_revision:0,data_filename:'S01_仿真.csv',data_identity:{kind:'synthetic'}})
    if(url==='/api/agent/history')return reply({messages:[],analysis_revision:1})
    if(url==='/api/analysis/results')return reply({analysis_revision:1,results_revision:0,black_spots:{result:{}},cross_validation:null,comprehensive:null,warnings:[]})
    if(url.startsWith('/api/data/preview'))return reply({columns:[],rows:[],total:0})
    throw Error(url)
  }
  const result=await store.loadSample('S01',true)
  const loadCall=calls.find(call=>call.url==='/api/data/samples/S01/load')
  assert.deepEqual(loadCall.body,{project_id:null,analysis_revision:0,results_revision:0,detect:true,river_form:{length_m:800}})
  assert.equal(result.rows,1801)
  assert.equal(store.status.rows,1801)
  assert.equal(store.projectState.current.id,projectState.current.id)
  assert.match(store.sampleLastReport,/S01 仿真正常背景/)
  assert.match(store.sampleLastReport,/筛查完成/)
  assert.equal(store.sampleLastDetection.ok,true)
})

test('载入失败不清空当前示例列表和状态，错误可显示',async()=>{
  const store=useSessionStore(createPinia())
  store.ready=true
  store.status={has_data:false,analysis_revision:0,results_revision:0}
  store.sampleCatalog=[sample]
  globalThis.fetch=async()=>reply({detail:'示例文件损坏'},503)
  await assert.rejects(store.loadSample('S01',false),/示例文件损坏/)
  assert.equal(store.sampleCatalog.length,1)
  assert.equal(store.loading,false)
})
