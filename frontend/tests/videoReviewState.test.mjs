import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {parse,compileScript} from '@vue/compiler-sfc'
import {createRenderer,nextTick,reactive} from 'vue'
const {descriptor}=parse(await readFile(new URL('../src/components/VideoQuality.vue',import.meta.url),'utf8'))
const compiled=compileScript(descriptor,{id:'video-review-regressions'})
const fixture='data:text/javascript,'+encodeURIComponent('export const api=globalThis.__videoApi;export const useSessionStore=()=>globalThis.__videoStore;export default {render:()=>null}')
const code=compiled.content.replace(/from ['"]([^'"]+)['"]/g,(_m,source)=>`from ${JSON.stringify(source==='vue'?import.meta.resolve('vue'):fixture)}`)
const renderer=createRenderer({createElement:tag=>({tag,children:[]}),createText:text=>({text}),createComment:text=>({text}),insert(n,p){p.children.push(n)},remove(){},setText(){},setElementText(){},parentNode:()=>null,nextSibling:()=>null,patchProp(){}})
const settings={roi:[0,0,1,1],roi_confirmed:false,reviewer:'',roi_note:'',sync_note:'',sync_confirmed:false,time_offset_sec:0,time_window_sec:30}
const row=id=>({id,time_sec:id*5,roi:[0,0,1,1],review:{decision:'pending',reviewer:'',notes:'',roi:null}})
const report=()=>({revision:'r'.repeat(64),settings:structuredClone(settings),rows:[row(0),row(1)],total:4,summary:{frames:4},notice:'current'})
const evidence=id=>({image_base64:'controlled',frame:row(id)})
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}
const settle=async()=>{await nextTick();await new Promise(resolve=>setImmediate(resolve));await nextTick()}
let serial=0
async function mount(){
  const saves=[]
  const api={videoQuality:async()=>report(),videoFrame:async id=>evidence(id),saveVideoSettings:async p=>{saves.push(p)},saveVideoFrame:async(id,p)=>{saves.push(p)}}
  const store=reactive({workspaceRevision:1,switchingProject:false,projectState:{current:{id:'project-a'}},status:{analysis_revision:1,video_frames:4,video_filename:'controlled',results_revision:1},loading:false,chatRunning:false,refreshStatus:async()=>{},
    async _run(fn){if(this.loading)throw Error('busy');this.loading=true;try{return await fn()}finally{this.loading=false}}})
  globalThis.__videoApi=api;globalThis.__videoStore=store
  const {default:Component}=await import('data:text/javascript;base64,'+Buffer.from(code.replaceAll(fixture,fixture+`#${++serial}`)).toString('base64'))
  Component.render=()=>null;const app=renderer.createApp(Component),vm=app.mount({children:[]});await settle()
  return {state:vm.$.setupState,store,api,saves,app}
}
test('加载单帧期间锁定复核，避免迟到帧覆盖编辑',async()=>{
  const v=await mount();try{const d=deferred();v.api.videoFrame=()=>d.promise;const pending=v.state.selectFrame(1)
    assert.equal(v.state.locked,true);d.resolve(evidence(1));await pending;assert.equal(v.state.locked,false)
  }finally{v.app.unmount()}
})
test('项目切换开始时即丢弃旧质量查询，不等待工作区编号递增',async()=>{
  const v=await mount();try{const d=deferred();v.api.videoQuality=()=>d.promise;const pending=v.state.load()
    v.store.switchingProject=true;await nextTick();d.resolve({...report(),notice:'old-project'});await pending
    assert.notEqual(v.state.report?.notice,'old-project')
  }finally{v.app.unmount()}
})
test('旧工作区帧请求的错误不污染新工作区',async()=>{
  const v=await mount();try{const d=deferred();v.api.videoFrame=id=>id===1?d.promise:Promise.resolve(evidence(id))
    const pending=v.state.selectFrame(1);v.store.workspaceRevision++;await nextTick();d.reject(Error('old frame error'));await pending;await settle()
    assert.notEqual(v.state.error,'old frame error')
  }finally{v.app.unmount()}
})
test('视频保存占用共享操作状态，项目切换和数据重载不能并行',async()=>{
  const v=await mount();try{const d=deferred();v.api.saveVideoSettings=()=>d.promise;v.state.changed()
    const pending=v.state.save();assert.equal(v.store.loading,true);d.resolve({});await pending;assert.equal(v.store.loading,false)
  }finally{v.app.unmount()}
})
test('脏草稿不允许改变区域应用范围，切换范围本身不制造脏草稿',async()=>{
  const v=await mount();try{assert.equal(typeof v.state.changeScope,'function');v.state.changeScope('frame')
    assert.equal(v.state.scope,'frame');assert.equal(v.state.dirty,false)
    v.state.changed();v.state.changeScope('all');assert.equal(v.state.scope,'frame');assert.equal(v.state.dirty,true)
  }finally{v.app.unmount()}
})
test('换成较短视频后自动回第一页，不遗留上一视频帧',async()=>{
  const v=await mount();try{const requested=[];v.api.videoQuality=async offset=>{requested.push(offset);return offset>0?{...report(),rows:[]}:report()}
    v.state.offset=20;await v.state.load();assert.equal(v.state.offset,0);assert.deepEqual(requested,[20,0]);assert.equal(v.state.selected,0)
  }finally{v.app.unmount()}
})
test('保存后刷新期间切换工作区，不能显示旧保存成功消息',async()=>{
  const v=await mount();try{const d=deferred();v.store.refreshStatus=()=>d.promise;v.state.changed();const pending=v.state.save();await settle()
    v.store.switchingProject=true;v.store.workspaceRevision++;await nextTick();d.resolve();await pending
    assert.ok(!v.state.message.includes('已更新当前会话'))
  }finally{v.app.unmount()}
})

test('项目切换失败且上下文未变时保留视频复核草稿',async()=>{
  const v=await mount();try{v.state.settings.roi_note='未保存的区域说明';v.state.changed()
    v.store.switchingProject=true;await settle();v.store.switchingProject=false;await settle()
    assert.equal(v.state.settings.roi_note,'未保存的区域说明');assert.equal(v.state.dirty,true)
  }finally{v.app.unmount()}
})
