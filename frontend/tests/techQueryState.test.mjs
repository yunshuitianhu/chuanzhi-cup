import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {parse,compileScript} from '@vue/compiler-sfc'
import {createRenderer,nextTick,reactive} from 'vue'
const fixture='data:text/javascript,'+encodeURIComponent('export const api=globalThis.__techApi;export const useSessionStore=()=>globalThis.__techStore;const events=globalThis.__techEvents;export const toastErr=m=>events.push(m);export const toastOk=m=>events.push(m)')
const codes={}
for(const name of ['MultiSourceTrace','StationQuery','SummaryCards']){
  const {descriptor}=parse(await readFile(new URL(`../src/components/${name}.vue`,import.meta.url),'utf8'))
  codes[name]=compileScript(descriptor,{id:`review-${name}`}).content.replace(/from ['"]([^'"]+)['"]/g,(_m,source)=>`from ${JSON.stringify(source==='vue'?import.meta.resolve('vue'):source==='../utils/traceRequest'?new URL('../src/utils/traceRequest.js',import.meta.url).href:fixture)}`)
}
const renderer=createRenderer({createElement:tag=>({tag,children:[]}),createText:text=>({text}),createComment:text=>({text}),insert(n,p){p.children.push(n)},remove(){},setText(){},setElementText(){},parentNode:()=>null,nextSibling:()=>null,patchProp(){}})
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}
const settle=async()=>{await nextTick();await new Promise(r=>setImmediate(r));await nextTick()}
const result=()=>({'站点数量':1,'站点列表':[],'综合污染源列表':[],marker:'first'})
let serial=0
async function mount(name){
  const events=[],calls=[]
  const api={provinces:async()=>({provinces:['广东省']}),multiSource:async()=>result(),queryStations:async()=>result(),summary:async()=>({marker:'initial'})}
  const store=reactive({ready:true,connecting:false,workspaceRevision:1,switchingProject:false,projectState:{current:{id:'a'}},status:{has_data:true,rows:1805,analysis_revision:1,results_revision:1},loading:false,chatRunning:false,refreshStatus:async()=>{}})
  store._run=async fn=>{calls.push('exclusive');if(store.loading||store.chatRunning)throw Error('busy');store.loading=true;try{return await fn()}finally{store.loading=false}}
  globalThis.__techApi=api;globalThis.__techStore=store;globalThis.__techEvents=events
  const code=codes[name].replaceAll(fixture,fixture+`#${++serial}`)
  const {default:Component}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'))
  Component.render=()=>null;const app=renderer.createApp(Component),vm=app.mount({children:[]});await settle()
  return {state:vm.$.setupState,api,store,app,events,calls}
}
for(const [name,method] of [['MultiSourceTrace','multiSource'],['StationQuery','queryStations']]){
  test(`${name} 丢弃同分析版本的旧项目响应`,async()=>{
    const v=await mount(name);try{const d=deferred();v.api[method]=()=>d.promise;const pending=v.state.run()
      v.store.switchingProject=true;v.store.projectState.current.id='b';await settle();d.resolve(result());await pending
      assert.equal(v.state.data,null);assert.equal(v.events.length,0)
    }finally{v.app.unmount()}
  })
  test(`${name} 参数变化后不接纳旧响应`,async()=>{
    const v=await mount(name);try{const d=deferred();v.api[method]=()=>d.promise;const pending=v.state.run()
      if(name==='MultiSourceTrace'){v.state.lat=30;v.state.lon=120}else v.state.keyword='新关键词'
      await settle();d.resolve(result());await pending;assert.equal(v.state.data,null)
    }finally{v.app.unmount()}
  })
  test(`${name} 失败查询撤掉上次结果并给出持久错误`,async()=>{
    const v=await mount(name);try{await v.state.run();assert.equal(v.state.data.marker,'first')
      v.api[method]=async()=>{throw Error('受控断网')};await v.state.run()
      assert.equal(v.state.data,null);assert.match(v.state.error,/受控断网/)
    }finally{v.app.unmount()}
  })
  test(`${name} 请求纳入全局互斥且忽略重复点击`,async()=>{
    const v=await mount(name);try{const d=deferred();let calls=0;v.api[method]=()=>{calls++;return d.promise};const pending=v.state.run()
      assert.equal(v.store.loading,true);await v.state.run();assert.equal(calls,1)
      d.resolve(result());await pending;assert.equal(v.store.loading,false);assert.equal(v.calls.length,1)
    }finally{v.app.unmount()}
  })
  test(`${name} 离开页面后失败请求不再弹出提示`,async()=>{
    const v=await mount(name);const d=deferred();v.api[method]=()=>d.promise;const pending=v.state.run();v.app.unmount()
    d.reject(Error('旧页面断网'));await pending;assert.equal(v.events.length,0)
  })
  test(`${name} 其他任务忙碌时不能发起查询`,async()=>{
    const v=await mount(name);try{let calls=0;v.api[method]=async()=>{calls++;return result()};v.store.loading=true;await v.state.run();assert.equal(calls,0)
    }finally{v.app.unmount()}
  })
}
test('站点查询已成功但状态刷新失败时保留查询结果并显示同步提示',async()=>{
  const v=await mount('StationQuery');try{v.store.refreshStatus=async()=>{throw Error('状态断网')};await v.state.run()
    assert.equal(v.state.data.marker,'first');assert.match(v.state.error,/状态.*刷新/)
  }finally{v.app.unmount()}
})
test('统计卡片在记录数和版本相同的项目之间重新加载',async()=>{
  const v=await mount('SummaryCards');try{v.api.summary=async()=>({marker:'new-project'});v.store.projectState.current.id='b';await settle();assert.equal(v.state.data.marker,'new-project')
  }finally{v.app.unmount()}
})
test('开始切换项目后统计卡片丢弃旧请求，切换结束才重新加载',async()=>{
  const v=await mount('SummaryCards');try{const d=deferred();v.api.summary=()=>d.promise;const pending=v.state.load()
    v.store.switchingProject=true;await settle();d.resolve({marker:'old'});await pending;assert.equal(v.state.data,null)
    v.api.summary=async()=>({marker:'new'});v.store.projectState.current.id='b';v.store.switchingProject=false;await settle();assert.equal(v.state.data.marker,'new')
  }finally{v.app.unmount()}
})


test('站点查询自身刷新状态对象时仍接纳本次结果',async()=>{
 const v=await mount('StationQuery')
 try{
  v.store.refreshStatus=async()=>{v.store.status={...v.store.status,results_revision:2,analysis_status:'stale'}}
  await v.state.run();await settle()
  assert.equal(v.state.data?.marker,'first')
  assert.equal(v.store.status.analysis_status,'stale')
  assert.ok(v.events.some(m=>/查询完成/.test(m)))
  assert.equal(v.state.loading,false)
 }finally{v.app.unmount()}
})
for(const name of ['StationQuery','MultiSourceTrace']){
 test(`${name} 同项目同数据的状态或项目对象替换不清空已显示结果`,async()=>{
  const v=await mount(name)
  try{
   await v.state.run();assert.equal(v.state.data?.marker,'first')
   v.store.status={...v.store.status,results_revision:2,analysis_status:'stale'}
   v.store.projectState={...v.store.projectState,current:{...v.store.projectState.current}}
   await settle();assert.equal(v.state.data?.marker,'first')
  }finally{v.app.unmount()}
 })
 test(`${name} 查询期间真正替换监测数据仍丢弃迟到结果`,async()=>{
  const v=await mount(name);const method=name==='StationQuery'?'queryStations':'multiSource'
  try{
   const d=deferred();v.api[method]=()=>d.promise;const pending=v.state.run()
   v.store.status={...v.store.status,analysis_revision:2};await settle()
   d.resolve(result());await pending
   assert.equal(v.state.data,null);assert.equal(v.events.length,0)
  }finally{v.app.unmount()}
 })
}
test('统计卡片忽略同一监测版本的普通状态刷新，避免查询后重复闪烁加载',async()=>{
 const v=await mount('SummaryCards')
 try{
  let reloads=0;v.api.summary=async()=>{reloads++;return {marker:'reloaded'}}
  v.store.status={...v.store.status,results_revision:2,analysis_status:'stale'};await settle()
  assert.equal(reloads,0);assert.equal(v.state.data?.marker,'initial')
 }finally{v.app.unmount()}
})
