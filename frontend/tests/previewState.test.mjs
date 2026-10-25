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

test('数据预览以最后发起的请求为准',async()=>{
  const store=useSessionStore(createPinia());store.status={has_data:true,analysis_revision:1};const releases=[]
  globalThis.fetch=()=>new Promise(resolve=>releases.push(body=>resolve(reply(body))))
  const first=store.refreshPreview(50),second=store.refreshPreview(10)
  releases[1]({columns:[],rows:[{marker:'new'}],total:1});await second
  releases[0]({columns:[],rows:[{marker:'old'}],total:1});await first;assert.equal(store.preview.rows[0].marker,'new')
})
test('项目切换一开始即作废旧数据预览请求',async()=>{
  const store=useSessionStore(createPinia());store.status={has_data:true,analysis_revision:1};let release
  globalThis.fetch=()=>new Promise(resolve=>{release=body=>resolve(reply(body))})
  const pending=store.refreshPreview();await assert.rejects(store.changeProject(async()=>{throw Error('切换失败')}))
  release({columns:[],rows:[{marker:'old'}],total:1});await pending;assert.deepEqual(store.preview.rows,[])
})
test('数据已清空时同步清空预览并拒绝迟到响应',async()=>{
  const store=useSessionStore(createPinia());store.status={has_data:true,analysis_revision:1};store.preview={columns:[],rows:[{marker:'shown'}],total:1};let release
  globalThis.fetch=()=>new Promise(resolve=>{release=body=>resolve(reply(body))})
  const pending=store.refreshPreview();store.status.has_data=false;await store.refreshPreview();assert.deepEqual(store.preview.rows,[])
  release({columns:[],rows:[{marker:'old'}],total:1});await pending;assert.deepEqual(store.preview.rows,[])
})
test('分析数据版本改变时不接纳旧预览',async()=>{
  const store=useSessionStore(createPinia());store.status={has_data:true,analysis_revision:1};let release
  globalThis.fetch=()=>new Promise(resolve=>{release=body=>resolve(reply(body))})
  const pending=store.refreshPreview();store.status.analysis_revision++;release({columns:[],rows:[{marker:'old'}],total:1});await pending;assert.deepEqual(store.preview.rows,[])
})


test('过期预览请求的失败不再干扰当前工作区',async()=>{
  const store=useSessionStore(createPinia());store.status={has_data:true,analysis_revision:1};let reject
  globalThis.fetch=()=>new Promise((resolve,fail)=>{reject=fail})
  const pending=store.refreshPreview();store.workspaceRevision++;reject(Error('旧请求断网'))
  await assert.doesNotReject(pending)
})
test('当前预览请求失败仍向调用者提供真实错误',async()=>{
  const store=useSessionStore(createPinia());store.status={has_data:true,analysis_revision:1}
  globalThis.fetch=async()=>{throw Error('当前请求断网')}
  await assert.rejects(store.refreshPreview(),/当前请求断网/)
})
