import test from 'node:test'
import assert from 'node:assert/strict'
import {createServer} from 'vite'
import {createPinia} from 'pinia'

globalThis.localStorage={getItem:()=>null,setItem:()=>{},removeItem:()=>{}}
const server=await createServer({configLoader:'runner',server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,entries:[]},appType:'custom',logLevel:'error',mode:'test'})
const {useSessionStore}=await server.ssrLoadModule('/src/stores/session.js')
test.after(async()=>{await server.close()})

const reply=(body,status=200)=>new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}})
const draft={title:'复核氨氮异常',hypothesis:'复测确认是否持续偏高',supporting_notes:'',opposing_notes:'',status:'open',retest:{location:'断面 A',latitude:30.1,longitude:120.2,indicators:['ammonia'],plan:'复测计划',observed_at:'',result:''},review:{outcome:'',reviewer:'',notes:''}}
const payload={preview_id:'preview-1',request_id:'request-1',draft}
const confirmResult=()=>({task_id:'controlled',board:{revision:1,items:[],persisted_project:true}})

function makeStore(){
  const store=useSessionStore(createPinia())
  store.ready=true
  store.switchingProject=false
  store.workspaceRevision=1
  store.status={analysis_revision:1,results_revision:1}
  return store
}

test('确认研判卡片通过实际 API 客户端发起 POST 并选中新任务（模拟网络响应）',async()=>{
  const store=makeStore()
  store.projectState.current=null
  const calls=[]
  globalThis.fetch=async(path,options)=>{calls.push({path,...options});return reply(confirmResult())}
  const saved=await store.confirmAnalysisCard('bundle-1','card-1',payload)
  assert.equal(calls.length,1)
  assert.equal(calls[0].method,'POST')
  assert.match(calls[0].path,/\/api\/agent\/evidence\/bundle-1\/cards\/card-1\/confirm$/)
  assert.deepEqual(JSON.parse(calls[0].body),payload)
  assert.equal(store.selectedInvestigationId,'controlled')
  assert.equal(store.investigationRevision,1)
  assert.equal(saved.task_id,'controlled')
})

test('确认期间项目已切换时不覆盖新选择',async()=>{
  const store=makeStore()
  store.projectState.current={id:'project-1'}
  let release
  globalThis.fetch=()=>new Promise(resolve=>{release=()=>resolve(reply(confirmResult()))})
  const pending=store.confirmAnalysisCard('bundle-1','card-1',payload)
  store.workspaceRevision++
  store.selectedInvestigationId='keep'
  release()
  const saved=await pending
  assert.equal(saved,null)
  assert.equal(store.selectedInvestigationId,'keep')
  assert.equal(store.investigationRevision,0)
})

test('确认已保存但项目列表刷新失败时返回警告且不重试写操作',async()=>{
  const store=makeStore()
  store.projectState.current={id:'project-1'}
  let writes=0,reads=0
  globalThis.fetch=async(path,options)=>{
    if(path==='/api/projects'){reads++;return reply({detail:'受控列表失败'},503)}
    assert.equal(options.method,'POST')
    writes++
    return reply({...confirmResult(),board:{revision:1,items:[],persisted_project:true}})
  }
  const saved=await store.confirmAnalysisCard('bundle-1','card-1',payload)
  assert.equal(writes,1)
  assert.equal(reads,1)
  assert.match(saved.refresh_warning,/记录已保存/)
  assert.equal(store.selectedInvestigationId,'controlled')
  assert.equal(store.investigationRevision,1)
  assert.equal(store.loading,false)
})
