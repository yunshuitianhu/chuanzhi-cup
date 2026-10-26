import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {pathToFileURL,fileURLToPath} from 'node:url'
import {createRequire} from 'node:module'
const project=process.env.Q13_PROJECT_ROOT||fileURLToPath(new URL('../../',import.meta.url))
const moduleRoot=process.env.Q13_MODULE_ROOT||project+'/frontend'
const require=createRequire(pathToFileURL(moduleRoot+'/package.json'))
const {parse,compileScript}=await import(pathToFileURL(require.resolve('@vue/compiler-sfc')).href)
const vueURL=pathToFileURL(moduleRoot+'/node_modules/vue/dist/vue.runtime.esm-bundler.js').href
const {createRenderer,h,nextTick,reactive}=await import(vueURL)
const renderer=createRenderer({createElement:tag=>({tag,children:[]}),createText:text=>({text}),createComment:text=>({text}),
 insert(n,p){p.children.push(n)},remove(){},setText(){},setElementText(){},parentNode:()=>null,nextSibling:()=>null,patchProp(){}})
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}
const settle=async()=>{await nextTick();await new Promise(r=>setImmediate(r));await nextTick()}
const id='a'.repeat(32),other='b'.repeat(32)
const card=(value=id,kind='sample')=>({id:value,kind,title:'current',summary:'observed',notice:'scope',selection:{sample_id:0},scope:{project_id:'a',analysis_revision:1,results_revision:1}})
const settings={roi:[0,0,1,1],roi_confirmed:false,roi_note:'',reviewer:'',sync_confirmed:false,sync_note:'',time_offset_sec:0,time_window_sec:30}
const row=id=>({id,time_sec:id*5,roi:[0,0,1,1],review:{decision:'pending',reviewer:'',notes:'',roi:null}})
const report=(offset=0)=>({revision:'r'.repeat(64),settings:structuredClone(settings),rows:Array.from({length:20},(_,i)=>row(i+offset)),total:40,summary:{frames:40},notice:'current'})
let serial=0
async function mount(name,initialProps={},query={}){
 const pushes=[],clears=[],requests=[],focuses=[],scrolls=[],frames=[]
 const props=reactive(initialProps),route=reactive({query}),store=reactive({ready:true,loading:false,chatRunning:false,switchingProject:false,workspaceRevision:1,
  projectState:{current:{id:'a'}},status:{has_data:true,analysis_revision:1,results_revision:1,rows:1805,video_frames:40,video_filename:'controlled'},config:{target_water_class:'V',keys:{}},
  async prepareQuestionContext(selector){requests.push(selector);return card()},
  clearQuestionContextById(value){clears.push(value)},refreshStatus:async()=>{},async _run(fn){store.loading=true;try{return await fn()}finally{store.loading=false}}})
 const api={ragStatus:async()=>({available:true}),ragSearch:async()=>({'检索结果':[],retrieval_id:'a'.repeat(32)}),questionContext:async value=>card(value),videoQuality:async offset=>report(offset),videoFrame:async frameId=>{frames.push(frameId);return {frame:row(frameId),image_base64:'fixture'}},
  timeSeries:async()=>({time:['2026-10-26T09:00:00','2026-10-26T10:00:00'],panels:['ammonia_nitrogen','dissolved_oxygen','turbidity','ph'].map(key=>({key,label:key,unit:'mg/L',color:'#fff',values:[1,2],limit:null})),black_spots:[],target_water_class:'V'})}
 const savedDocument=globalThis.document
 globalThis.document={getElementById:key=>({scrollIntoView:()=>scrolls.push(key)})}
 globalThis.__q13Source={route,store,api,pushes}
 const fixture='data:text/javascript,'+encodeURIComponent('export const useRouter=()=>({push:async p=>globalThis.__q13Source.pushes.push(p)});export const useRoute=()=>globalThis.__q13Source.route;export const useSessionStore=()=>globalThis.__q13Source.store;export const api=globalThis.__q13Source.api;export const toastErr=()=>{};export const toastOk=()=>{};export default {render:()=>null}')+'#'+(++serial)
 const {descriptor}=parse(await readFile(project+'/frontend/src/'+(name.endsWith('View')?'views/':'components/')+name+'.vue','utf8'))
 const code=compileScript(descriptor,{id:'q13-'+name}).content.replace(/from ['"]([^'"]+)['"]/g,(_m,dep)=>{
  const url=dep==='vue'?vueURL:dep==='../utils/questionContextSelection'?pathToFileURL(project+'/frontend/src/utils/questionContextSelection.js').href:fixture
  return 'from '+JSON.stringify(url)
 })
 const {default:Component}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'))
 Component.render=()=>null
 const app=renderer.createApp({render:()=>h(Component,{...props,onFocus:value=>focuses.push(value)})})
 const vm=app.mount({children:[]})
 await settle()
 return {state:vm.$.subTree.component.setupState,store,api,route,props,pushes,clears,requests,focuses,scrolls,frames,app,
  close(){app.unmount();globalThis.document=savedDocument}}
}

test('采样点入口只传选择标识，准备完成后导航，不直接发模型请求',async()=>{
 const v=await mount('AskContext',{selector:{kind:'sample',sample_id:7}})
 try{await v.state.ask();assert.deepEqual(v.requests,[{kind:'sample',sample_id:7}]);assert.deepEqual(v.pushes,['/chat'])}finally{v.close()}
})
test('准备期间切换项目，不采用迟到结果或导航',async()=>{
 const v=await mount('AskContext',{selector:{kind:'sample',sample_id:7}})
 try{const d=deferred();v.store.prepareQuestionContext=()=>d.promise;const pending=v.state.ask();v.store.switchingProject=true;v.store.projectState.current.id='b';await settle();d.resolve(null);await pending;assert.deepEqual(v.pushes,[])}finally{v.close()}
})
test('准备结果为空不导航，也不清除别的选择',async()=>{
 const v=await mount('AskContext',{selector:{kind:'sample',sample_id:0}})
 try{v.store.prepareQuestionContext=async()=>null;await v.state.ask();assert.deepEqual(v.pushes,[]);assert.deepEqual(v.clears,[])}finally{v.close()}
})
test('准备期间源选择改变，按id撤销该引用而不导航',async()=>{
 const v=await mount('AskContext',{selector:{kind:'sample',sample_id:0}})
 try{const d=deferred();v.store.prepareQuestionContext=()=>d.promise;const pending=v.state.ask();v.props.selector.sample_id=1;d.resolve(card());await pending;assert.deepEqual(v.clears,[id]);assert.deepEqual(v.pushes,[]);assert.match(v.state.error,/选择已经变化/)}finally{v.close()}
})
test('分析执行中不会准备新的上下文',async()=>{
 const v=await mount('AskContext',{selector:{kind:'sample',sample_id:0}})
 try{v.store.chatRunning=true;await v.state.ask();assert.equal(v.requests.length,0)}finally{v.close()}
})
test('真实的来源准备失败保留提示且不导航',async()=>{
 const v=await mount('AskContext',{selector:{kind:'sample',sample_id:0}})
 try{v.store.prepareQuestionContext=async()=>{throw Error('版本过期')};await v.state.ask();assert.equal(v.state.error,'版本过期');assert.equal(v.pushes.length,0)}finally{v.close()}
})
test('来源返回校验通过后才发出定位事件',async()=>{
 const v=await mount('ContextSourceFocus',{kinds:['sample']},{context_id:id})
 try{assert.equal(v.state.card.id,id);assert.equal(v.focuses.at(-1).selection.sample_id,0);assert.deepEqual(v.scrolls,['trace-map'])}finally{v.close()}
})
test('来源过期时撤掉旧指向并显示错误',async()=>{
 const v=await mount('ContextSourceFocus',{kinds:['sample']},{context_id:id})
 try{v.api.questionContext=async()=>{throw Error('该引用已过期')};await v.state.load();assert.equal(v.state.card,null);assert.equal(v.focuses.at(-1),null);assert.match(v.state.error,/已过期/)}finally{v.close()}
})
test('来源核对期间项目切换，旧响应不能重新定位',async()=>{
 const v=await mount('ContextSourceFocus',{kinds:['sample']},{context_id:id})
 try{const before=v.scrolls.length,d=deferred();v.api.questionContext=()=>d.promise;const pending=v.state.load();v.store.switchingProject=true;v.store.workspaceRevision++;await settle();d.resolve(card());await pending;assert.equal(v.state.card,null);assert.equal(v.scrolls.length,before)}finally{v.close()}
})
test('不接受当前页面以外的引用kind',async()=>{
 const v=await mount('ContextSourceFocus',{kinds:['sample']},{context_id:id})
 try{v.api.questionContext=async value=>card(value,'rag_excerpt');await v.state.load();assert.equal(v.state.card,null);assert.match(v.state.error,/不属于本页面/)}finally{v.close()}
})
test('同编号但不同视频版本不会定位引用帧',async()=>{
 const v=await mount('VideoQuality',{focusFrameId:25,focusVideoRevision:'old'})
 try{assert.equal(v.state.selected,0);assert.ok(!v.frames.includes(25));assert.match(v.state.message,/版本或编号/)}finally{v.close()}
})
test('核对版本一致的跨页视频帧自动加载对应页',async()=>{
 const v=await mount('VideoQuality',{focusFrameId:25,focusVideoRevision:'r'.repeat(64)})
 try{assert.equal(v.state.offset,20);assert.equal(v.state.selected,25);assert.equal(v.frames.at(-1),25)}finally{v.close()}
})
test('引用帧变化时保留未保存的ROI草稿',async()=>{
 const v=await mount('VideoQuality',{focusFrameId:0,focusVideoRevision:'r'.repeat(64)})
 try{v.state.settings.roi_note='未保存';v.state.changed();v.props.focusFrameId=25;await settle();assert.equal(v.state.selected,0);assert.equal(v.state.settings.roi_note,'未保存');assert.equal(v.state.dirty,true);assert.match(v.state.message,/草稿尚未保存/)}finally{v.close()}
})
test('图表范围选择只改时间草稿，不重建趋势option',async()=>{
 const v=await mount('ChartsView')
 try{const panels=v.state.panels;v.state.applyZoom('dissolved_oxygen',{start:25,end:75});assert.equal(v.state.rangeIndicator,'dissolved_oxygen');assert.ok(v.state.rangeSelector.start.includes('09:15:00'));assert.strictEqual(v.state.panels,panels)}finally{v.close()}
})
test('从来源返回的时间范围不被迟到图表加载默认值覆盖',async()=>{
 const v=await mount('ChartsView')
 try{const d=deferred();v.api.timeSeries=()=>d.promise;const pending=v.state.loadTimeSeries();v.state.acceptContext({...card(id,'time_range'),selection:{start:'2026-10-26T09:20:00',end:'2026-10-26T09:30:00',indicator:'ph'}});d.resolve({time:['2026-10-26T09:00:00','2026-10-26T10:00:00'],panels:[],black_spots:[]});await pending;assert.equal(v.state.rangeStart,'2026-10-26T09:20:00');assert.equal(v.state.rangeEnd,'2026-10-26T09:30:00')}finally{v.close()}
})

test('已核对来源不会因准备追问占用loading而清空原选择',async()=>{
 const v=await mount('ContextSourceFocus',{kinds:['sample']},{context_id:id})
 try{const count=v.focuses.length;v.store.loading=true;await settle();assert.equal(v.state.card.id,id);assert.equal(v.focuses.length,count);v.store.loading=false;await settle();assert.equal(v.focuses.length,count)}finally{v.close()}
})


test('RAG检索占用共享操作状态，不能与来源准备或项目切换并行',async()=>{
 const v=await mount('RagSearch')
 try{const d=deferred();v.api.ragSearch=()=>d.promise;const pending=v.state.run('氨氮');assert.equal(v.store.loading,true);d.resolve({'检索结果':[],retrieval_id:id});await pending;assert.equal(v.store.loading,false);assert.equal(v.state.data.retrieval_id,id)}finally{v.close()}
})
test('RAG检索在同记录数跨项目切换后丢弃迟到片段和引用令牌',async()=>{
 const v=await mount('RagSearch')
 try{const d=deferred();v.api.ragSearch=()=>d.promise;const pending=v.state.run('氨氮');v.store.projectState.current.id='b';await settle();d.resolve({retrieval_id:id});await pending;assert.equal(v.state.data,null)}finally{v.close()}
})
test('RAG已展示片段在分析版本改变后清除，不能进入新项目追问',async()=>{
 const v=await mount('RagSearch')
 try{await v.state.run('氨氮');assert.ok(v.state.data);v.store.status.results_revision++;await settle();assert.equal(v.state.data,null)}finally{v.close()}
})
test('RAG检索期间查询条件变化，旧片段不覆盖新条件',async()=>{
 const v=await mount('RagSearch')
 try{const d=deferred();v.api.ragSearch=()=>d.promise;const pending=v.state.run('氨氮');v.state.query='溶解氧';d.resolve({retrieval_id:id});await pending;assert.equal(v.state.data,null)}finally{v.close()}
})
test('RAG卸载后不采用迟到结果，已有分析任务时不启动检索',async()=>{
 const v=await mount('RagSearch');let calls=0
 try{v.api.ragSearch=async()=>{calls++;return {retrieval_id:id}};v.store.chatRunning=true;await v.state.run('氨氮');assert.equal(calls,0);v.store.chatRunning=false;const d=deferred();v.api.ragSearch=()=>d.promise;const pending=v.state.run('氨氮');v.app.unmount();d.resolve({retrieval_id:id});await pending;assert.equal(v.state.data,null)}finally{v.close()}
})
