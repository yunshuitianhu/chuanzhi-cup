import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {parse,compileScript} from '@vue/compiler-sfc'
import {createRenderer,reactive,nextTick} from 'vue'
import {textSourceNotice,withoutTextSource} from '../src/utils/textSourceNotice.js'
const vueURL=import.meta.resolve('vue')
const {descriptor}=parse(await readFile(new URL('../src/components/TextSourceReturn.vue',import.meta.url),'utf8'))
const compiled=compileScript(descriptor,{id:'source-return-review'}).content
const renderer=createRenderer({createElement:tag=>({tag,children:[]}),createText:text=>({text}),createComment:text=>({text}),insert(n,p){p.children.push(n)},remove(){},setText(){},setElementText(){},parentNode:()=>null,nextSibling:()=>null,patchProp(){}})
const settle=async()=>{await nextTick();await new Promise(r=>setImmediate(r));await nextTick()}
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {resolve,reject,promise}}
const id='a'.repeat(32),other='b'.repeat(32)
const card=()=>({id,kind:'text_quote',scope:{project_id:'p',analysis_revision:1,results_revision:1},selection:{source:{kind:'page',anchor:'model',path:'/tech'}}})
let serial=0
async function mount(query={text_context:id},impl=async()=>card()){
 const store=reactive({ready:true,switchingProject:false,workspaceRevision:1,projectState:{current:{id:'p'}},status:{analysis_revision:1,results_revision:1},textQuestions:{sourceCard:null,sourceError:'',drafts:{thread:'保留的侧边草稿'}},chatDraft:'保留的主草稿'})
 const route=reactive({path:'/tech',query,hash:'#water-quality-grid'}),calls=[],replacements=[],focuses=[]
 const api={async questionContext(value){calls.push(value);return impl(value)}}
 const router={async replace(value){replacements.push(value);Object.assign(route,value)}}
 const saved=globalThis.document;globalThis.document={getElementById:value=>({scrollIntoView(){focuses.push(value)},focus(){}})}
 globalThis.__sourceNotice={store,route,router,api}
 const fixture='data:text/javascript,'+encodeURIComponent('export const useRouter=()=>globalThis.__sourceNotice.router;export const useRoute=()=>globalThis.__sourceNotice.route;export const useSessionStore=()=>globalThis.__sourceNotice.store;export const api=globalThis.__sourceNotice.api;export default {render:()=>null}')+'#'+(++serial)
 const code=compiled.replace(/from ['"]([^'"]+)['"]/g,(_m,dep)=>'from '+JSON.stringify(dep==='vue'?vueURL:dep.startsWith('../utils/')?new URL('../src/utils/'+dep.split('/').at(-1)+'.js',import.meta.url).href:fixture))
 const {default:Component}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'))
 Component.render=()=>null;const app=renderer.createApp(Component),vm=app.mount({children:[]});await settle()
 return {store,route,router,api,calls,replacements,focuses,state:vm.$.setupState,close(){app.unmount();globalThis.document=saved;delete globalThis.__sourceNotice}}
}

test('错误提示区分版本变化、链接失效和网络失败，不泄露服务端诊断文本',()=>{
 for(const status of [404,409,422,500,undefined]){
  const result=textSourceNotice({status,message:'PRIVATE_INTERNAL_ERROR'})
  assert.doesNotMatch(JSON.stringify(result),/PRIVATE_INTERNAL_ERROR|历史卡片/)
  assert.equal(result.retryable,![404,409,422].includes(status))
 }
 assert.match(textSourceNotice({status:409}).message,/当前结果已更新/)
 assert.match(textSourceNotice({status:404}).message,/重启服务或恢复项目/)
})

test('关闭引用只清理专属查询参数，保留页面、筛选与锚点',()=>{
 const route={path:'/tech',query:{text_context:id,filter:['one','two']},hash:'#water-quality-grid'}
 assert.deepEqual(withoutTextSource(route),{path:'/tech',query:{filter:['one','two']},hash:route.hash})
 assert.equal(route.query.text_context,id)
})

test('匹配版本的引用仍可定位，重复相同状态不会重新请求',async()=>{
 const v=await mount()
 try{assert.equal(v.store.textQuestions.sourceCard.id,id);assert.deepEqual(v.focuses,['model']);v.store.status={...v.store.status};await settle();assert.equal(v.calls.length,1)}finally{v.close()}
})

test('版本改变显示重选说明，不再显示旧卡片或原始409错误',async()=>{
 let count=0;const v=await mount(undefined,async()=>{if(++count>1)throw Object.assign(Error('历史卡片不能关联'),{status:409});return card()})
 try{v.store.status.results_revision=2;await settle();assert.equal(v.store.textQuestions.sourceCard,null);assert.equal(v.store.textQuestions.sourceError.title,'这段引用需要重新选择');assert.doesNotMatch(v.store.textQuestions.sourceError.message,/历史卡片/)}finally{v.close()}
})

test('前端发现不匹配版本同样拒绝旧引用',async()=>{
 const v=await mount(undefined,async()=>({...card(),scope:{...card().scope,results_revision:0}}))
 try{assert.equal(v.store.textQuestions.sourceCard,null);assert.equal(v.store.textQuestions.sourceError.retryable,false)}finally{v.close()}
})

test('无效链接不请求API；关闭后不复现且保留两个草稿',async()=>{
 const v=await mount({text_context:'broken',filter:'keep'})
 try{assert.equal(v.calls.length,0);await v.state.dismiss();await settle();assert.deepEqual(v.route.query,{filter:'keep'});assert.equal(v.route.hash,'#water-quality-grid');v.store.status.results_revision++;await settle();assert.equal(v.calls.length,0);assert.equal(v.store.textQuestions.sourceError,'');assert.equal(v.store.chatDraft,'保留的主草稿');assert.equal(v.store.textQuestions.drafts.thread,'保留的侧边草稿')}finally{v.close()}
})

test('网络错误可重试，成功后清除提示且不发送模型问题',async()=>{
 let count=0;const v=await mount(undefined,async()=>{if(++count===1)throw Error('network');return card()})
 try{assert.equal(v.store.textQuestions.sourceError.retryable,true);await v.state.source();assert.equal(v.store.textQuestions.sourceError,'');assert.equal(v.store.textQuestions.sourceCard.id,id);assert.equal(v.calls.length,2)}finally{v.close()}
})

test('切换来源后迟到的失败不会覆盖新引用',async()=>{
 const d=deferred();const v=await mount(undefined,value=>value===id?d.promise:Promise.resolve({...card(),id:other}))
 try{v.route.query={text_context:other};await settle();d.reject(Object.assign(Error('old'),{status:409}));await settle();assert.equal(v.store.textQuestions.sourceCard.id,other);assert.equal(v.store.textQuestions.sourceError,'')}finally{d.resolve(card());v.close()}
})

test('关闭提示会取消迟到请求的写回',async()=>{
 const d=deferred();const v=await mount(undefined,()=>d.promise)
 try{await v.state.dismiss();d.reject(Error('late'));await settle();assert.equal(v.store.textQuestions.sourceCard,null);assert.equal(v.store.textQuestions.sourceError,'')}finally{d.resolve(card());v.close()}
})

test('关闭时导航被取消，显示可恢复提示而非假装清理了链接',async()=>{
 const v=await mount({text_context:'broken'})
 try{v.router.replace=async()=>({type:4});await v.state.dismiss();assert.equal(v.route.query.text_context,'broken');assert.equal(v.store.textQuestions.sourceError.title,'暂时无法关闭提示');assert.equal(v.state.closing,false)}finally{v.close()}
})

test('切换项目后不采用旧项目的迟到失败',async()=>{
 const d=deferred();const v=await mount(undefined,()=>d.promise)
 try{v.store.switchingProject=true;v.store.workspaceRevision++;await settle();d.reject(Error('old project'));await settle();assert.equal(v.store.textQuestions.sourceCard,null);assert.equal(v.store.textQuestions.sourceError,'')}finally{d.resolve(card());v.close()}
})
