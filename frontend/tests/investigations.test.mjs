import test from 'node:test'
import assert from 'node:assert/strict'
import {createServer} from 'vite'
import {createPinia} from 'pinia'
import {createSSRApp} from 'vue'
import {renderToString} from '@vue/server-renderer'

globalThis.localStorage={getItem:()=>null,setItem:()=>{},removeItem:()=>{}}
const server=await createServer({configLoader:'runner',server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,entries:[]},appType:'custom',logLevel:'error',mode:'test'})
const {useSessionStore}=await server.ssrLoadModule('/src/stores/session.js')
test.after(async()=>{await server.close()})
const reply=(body,status=200)=>new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}})
const result={task_id:'controlled',board:{revision:1,items:[],persisted_project:false}}

test('排查任务使用服务端版本创建，保存后选中新任务',async()=>{
  const store=useSessionStore(createPinia());store.ready=true
  const calls=[]
  globalThis.fetch=async(path,options)=>{
    calls.push({path,...options})
    return path==='/api/investigations' && options.method==='GET'?reply({scope_id:'server-scope',revision:7}):reply(result)
  }
  await store.investigateRecord('report-A',2)
  assert.equal(calls.length,2)
  assert.deepEqual(JSON.parse(calls[1].body),{scope_id:'server-scope',expected_revision:7,reference:{bundle_id:'report-A',position:2}})
  assert.equal(store.selectedInvestigationId,'controlled');assert.equal(store.investigationRevision,1)
})

test('读取排查版本后项目已变化，不再创建任务',async()=>{
  const store=useSessionStore(createPinia());store.ready=true
  let release,calls=0
  globalThis.fetch=()=>{calls++;return new Promise(resolve=>{release=()=>resolve(reply({scope_id:'old',revision:0}))})}
  const pending=store.investigateRecord('old-report',0)
  store.workspaceRevision++;release()
  await assert.rejects(pending,/项目已切换/)
  assert.equal(calls,1);assert.equal(store.investigationRevision,0)
})

test('保存任务的迟到响应不覆盖新项目选择',async()=>{
  const store=useSessionStore(createPinia());store.ready=true
  let release
  globalThis.fetch=()=>new Promise(resolve=>{release=()=>resolve(reply(result))})
  const pending=store.saveInvestigation('old-task',{scope_id:'old',expected_revision:0,draft:{}})
  store.workspaceRevision++;store.selectedInvestigationId='new-task';release()
  await assert.rejects(pending,/项目已切换/)
  assert.equal(store.selectedInvestigationId,'new-task');assert.equal(store.investigationRevision,0)
})

test('已保存记录在项目列表刷新失败时仍报告保存成功，不重试写操作',async()=>{
  const store=useSessionStore(createPinia());store.ready=true;store.projectState.current={id:'project'}
  let writes=0
  globalThis.fetch=async(path,options)=>{
    if(path==='/api/projects')return reply({detail:'受控列表失败'},503)
    assert.equal(options.method,'PUT');writes++;return reply({...result,board:{...result.board,persisted_project:true}})
  }
  const saved=await store.saveInvestigation('controlled',{scope_id:'scope',expected_revision:0,draft:{}})
  assert.equal(writes,1);assert.match(saved.refresh_warning,/记录已保存/)
  assert.equal(store.selectedInvestigationId,'controlled');assert.equal(store.loading,false)
})

test('冲突拒绝保留任务版本，不伪报已保存',async()=>{
  const store=useSessionStore(createPinia());store.ready=true;store.selectedInvestigationId='existing'
  let calls=0
  globalThis.fetch=async()=>{calls++;return reply({detail:'数据、项目或排查记录已变化'},409)}
  await assert.rejects(store.saveInvestigation('',{}),/已变化/)
  assert.equal(calls,1);assert.equal(store.investigationRevision,0);assert.equal(store.selectedInvestigationId,'existing')
})

test('排查入口解释人工复核和临时会话边界，运行中不可编辑',async()=>{
  const {default:Board}=await server.ssrLoadModule('/src/components/InvestigationBoard.vue')
  const pinia=createPinia(),store=useSessionStore(pinia);store.chatRunning=true
  const app=createSSRApp(Board);app.use(pinia)
  const html=await renderToString(app)
  assert.match(html,/结束排查不代表确认污染源或责任/)
  assert.match(html,/分析执行中/)
  assert.match(html,/<button disabled[^>]*>新建排查任务/)
})
