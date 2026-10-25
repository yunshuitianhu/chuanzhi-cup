import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {parse,compileScript} from '@vue/compiler-sfc'
import {createRenderer,nextTick,reactive} from 'vue'
const {descriptor}=parse(await readFile(new URL('../src/components/DataQualityPanel.vue',import.meta.url),'utf8'))
const compiled=compileScript(descriptor,{id:'data-quality-review'})
const fixture='data:text/javascript,'+encodeURIComponent('export const api=globalThis.__qcApi;export const useSessionStore=()=>globalThis.__qcStore;export default {render:()=>null}')
const code=compiled.content.replace(/from ['"]([^'"]+)['"]/g,(_m,source)=>`from ${JSON.stringify(source==='vue'?import.meta.resolve('vue'):fixture)}`)
const renderer=createRenderer({createElement:tag=>({tag,children:[]}),createText:text=>({text}),createComment:text=>({text}),insert(n,p){p.children.push(n)},remove(){},setText(){},setElementText(){},parentNode:()=>null,nextSibling:()=>null,patchProp(){}})
const config=()=>({max_gap_s:60,flat_duration_s:120,flat_min_points:5,spike_thresholds:{ammonia_nitrogen:2,dissolved_oxygen:.5},flat_tolerances:{ammonia_nitrogen:.001,dissolved_oxygen:.001}})
const response=value=>({summary:{'参数':value||config()},records:[],total:120})
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}
const settle=async()=>{await nextTick();await new Promise(resolve=>setImmediate(resolve));await nextTick()}
let serial=0
async function mount(){
  const applied=[];const api={dataQuality:async()=>response()}
  const store=reactive({workspaceRevision:1,switchingProject:false,projectState:{current:{id:'a'}},status:{has_data:true,analysis_revision:1,results_revision:1,rows:1805},loading:false,chatRunning:false,updateQualityConfig:async value=>{applied.push(value)}})
  globalThis.__qcApi=api;globalThis.__qcStore=store
  const {default:Component}=await import('data:text/javascript;base64,'+Buffer.from(code.replaceAll(fixture,fixture+`#${++serial}`)).toString('base64'))
  Component.render=()=>null;const app=renderer.createApp(Component),vm=app.mount({children:[]});await settle()
  return {state:vm.$.setupState,api,store,app,applied}
}
test('质控查询在项目切换开始时即丢弃旧响应',async()=>{
  const v=await mount();try{const d=deferred();v.api.dataQuality=()=>d.promise;const pending=v.state.load()
    v.store.switchingProject=true;v.store.projectState.current.id='b';await nextTick();d.resolve({...response(),marker:'old'});await pending
    assert.notEqual(v.state.result?.marker,'old')
  }finally{v.app.unmount()}
})
test('质控保存期间锁定表单，避免覆盖保存后继续编辑的输入',async()=>{
  const v=await mount();try{const d=deferred();v.store.updateQualityConfig=()=>d.promise;const pending=v.state.saveSettings()
    assert.equal(v.state.busy,true);d.resolve({});await pending;assert.equal(v.state.busy,false)
  }finally{v.app.unmount()}
})
test('旧项目保存完成不能给新项目显示应用成功',async()=>{
  const v=await mount();try{const d=deferred();v.store.updateQualityConfig=()=>d.promise;const pending=v.state.saveSettings()
    v.store.workspaceRevision++;v.store.projectState.current.id='b';await settle();d.resolve({});await pending
    assert.equal(v.state.saved,false)
  }finally{v.app.unmount()}
})
test('无关分析结果刷新保留质控参数草稿',async()=>{
  const v=await mount();try{v.state.settings.spike_thresholds.ammonia_nitrogen=3
    v.store.status.results_revision++;await settle()
    assert.equal(v.state.settings.spike_thresholds.ammonia_nitrogen,3)
  }finally{v.app.unmount()}
})
test('后台质控参数变化时保护草稿并阻止覆盖新配置',async()=>{
  const v=await mount();try{v.state.settings.spike_thresholds.ammonia_nitrogen=3
    const other=config();other.spike_thresholds.ammonia_nitrogen=4;v.api.dataQuality=async()=>response(other)
    v.store.status.results_revision++;await settle();await v.state.saveSettings()
    assert.equal(v.state.settings.spike_thresholds.ammonia_nitrogen,3)
    assert.equal(v.state.settingsConflict,true);assert.equal(v.applied.length,0)
  }finally{v.app.unmount()}
})
test('质控分页忽略加载中的重复点击并禁止负偏移',async()=>{
  const v=await mount();try{const d=deferred(),offsets=[];v.api.dataQuality=payload=>{offsets.push(payload.offset);return d.promise}
    v.state.page(-1);assert.equal(v.state.offset,0);assert.equal(offsets.length,0)
    v.state.page(1);v.state.page(1);assert.equal(v.state.offset,50);assert.deepEqual(offsets,[50])
    d.resolve(response());await settle()
  }finally{v.app.unmount()}
})

test('项目切换失败且上下文未变时保留质控草稿',async()=>{
  const v=await mount();try{v.state.settings.spike_thresholds.ammonia_nitrogen=3
    v.store.switchingProject=true;await settle();v.store.switchingProject=false;await settle()
    assert.equal(v.state.settings.spike_thresholds.ammonia_nitrogen,3)
  }finally{v.app.unmount()}
})
