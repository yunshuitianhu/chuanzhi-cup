// 使用Vite加载实际Pinia模块；仅模拟HTTP响应，不启动或控制浏览器。
import test from 'node:test'
import assert from 'node:assert/strict'
import { createServer } from 'vite'
import { createPinia } from 'pinia'

const memory = new Map()
globalThis.localStorage = { getItem:k=>memory.get(k)||null, setItem:(k,v)=>memory.set(k,v), removeItem:k=>memory.delete(k) }
const server = await createServer({ server:{middlewareMode:true}, appType:'custom', logLevel:'error', mode:'test' })
const { useSessionStore } = await server.ssrLoadModule('/src/stores/session.js')
test.after(async () => { await server.close() })
const reply = (body, status=200) => new Response(JSON.stringify(body),{
  status, headers:{'Content-Type':'application/json','X-Session-Id':'test-session'},
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
