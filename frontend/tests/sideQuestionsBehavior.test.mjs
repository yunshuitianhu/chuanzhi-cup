import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {pathToFileURL} from 'node:url'
import {parse,compileScript} from '@vue/compiler-sfc'
import {createRenderer,reactive,nextTick} from 'vue'
import {quoteKey,textQuestionState} from '../src/utils/textQuestions.js'
const vueURL=import.meta.resolve('vue')
const {descriptor}=parse(await readFile(new URL('../src/components/SideQuestions.vue',import.meta.url),'utf8'))
const compiled=compileScript(descriptor,{id:'side-question-review'}).content
const renderer=createRenderer({createElement:tag=>({tag,children:[]}),createText:text=>({text}),createComment:text=>({text}),insert(n,p){p.children.push(n)},remove(){},setText(){},setElementText(){},parentNode:()=>null,nextSibling:()=>null,patchProp(){}})
const settle=async()=>{await nextTick();await new Promise(r=>setImmediate(r));await nextTick()}
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {resolve,promise}}
const a='a'.repeat(32),b='b'.repeat(32)
const quote={text:'所选文字',title:'A',source:{kind:'help',help_id:'rag_help'},project_id:'p',analysis_revision:1,results_revision:1}
let serial=0
async function mount(){
  const q=reactive(textQuestionState()),store=reactive({textQuestions:q,ready:true,loading:false,chatRunning:false,chatStreaming:false,sideRunning:false,workspaceRevision:1,projectState:{current:{id:'p'}},switchingProject:false,
    async prepareSideThread(){q.active=a;return {id:a,quote:{title:'A'}}},
    async loadSideThreads(){store.sideRunning=false;store.chatRunning=false}})
  q.pendingSide=structuredClone(quote);q.drafts[quoteKey(quote)]='原始问题'
  const saved={document:globalThis.document,matchMedia:globalThis.matchMedia,fetch:globalThis.fetch}
  globalThis.document={body:{style:{overflow:''}},querySelector:()=>null,activeElement:null}
  globalThis.matchMedia=()=>({matches:false,onchange:null})
  globalThis.fetch=async()=>({ok:true,body:{},headers:{get:()=> 'run'}})
  const service={store,api:{},consume:async(_body,callback)=>{callback('delta',{thread_id:a,text:'A的回答'});callback('done',{thread_id:a,status:'succeeded'})}}
  globalThis.__sideReview=service
  const fixture='data:text/javascript,'+encodeURIComponent('export const useRouter=()=>({push:async()=>{}});export const useSessionStore=()=>globalThis.__sideReview.store;export const api=globalThis.__sideReview.api;export const sseHeaders=()=>({});export const adoptSessionFrom=()=>{};export const consumeAgentStream=(...args)=>globalThis.__sideReview.consume(...args);export default {render:()=>null}')+'#'+(++serial)
  const code=compiled.replace(/from ['"]([^'"]+)['"]/g,(_m,dep)=>'from '+JSON.stringify(dep==='vue'?vueURL:dep==='../utils/textQuestions'?new URL('../src/utils/textQuestions.js',import.meta.url).href:fixture))
  const {default:Component}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'))
  Component.render=()=>null;const app=renderer.createApp(Component),vm=app.mount({children:[]});await settle()
  return {q,store,service,state:vm.$.setupState,async settle(){await settle()},close(){app.unmount();Object.assign(globalThis,saved);delete globalThis.__sideReview}}
}
test('发送准备期间继续编辑的问题不会被清空',async()=>{
  const v=await mount(),d=deferred()
  try{v.store.prepareSideThread=async()=>{await d.promise;v.q.active=a;v.q.drafts[a]='后来输入的新问题';return {id:a,quote:{title:'A'}}}
    const pending=v.state.send();v.q.drafts[quoteKey(quote)]='后来输入的新问题';d.resolve();await pending
    assert.equal(v.q.drafts[a],'后来输入的新问题')
  }finally{v.close()}
})
test('A流式回复期间改选B引用，不把A的增量显示成B的回复',async()=>{
  const v=await mount(),d=deferred()
  try{v.service.consume=async(_body,callback)=>{callback('delta',{thread_id:a,text:'A的回答'});await d.promise;callback('done',{thread_id:a,status:'succeeded'})}
    const pending=v.state.send();await v.settle();v.q.active=b;await v.settle()
    assert.equal(v.state.visiblePartial,'');v.q.active=a;assert.equal(v.state.visiblePartial,'A的回答')
    d.resolve();await pending
  }finally{d.resolve();v.close()}
})
test('后台线程的保存提示不冒充当前未发送引用已经保存',async()=>{
  const v=await mount()
  try{v.q.notice='本轮侧边问答已随项目保存。';v.q.run={run_id:'run',thread_id:a,busy:false,status:'succeeded'};v.q.active=''
    assert.match(v.state.visibleNotice,/另一|上次|上一/)
  }finally{v.close()}
})
test('失败问题可查看，不能覆盖已写的新草稿或冒充另一线程出错',async()=>{
  const v=await mount(),d=deferred()
  try{v.service.consume=async(_body,callback)=>{await d.promise;callback('error',{thread_id:a,message:'连接失败'})}
    const pending=v.state.send();await v.settle();v.q.active=b;v.q.drafts[a]='A的新草稿';v.q.drafts[b]='B的新草稿'
    d.resolve();await pending
    assert.equal(v.q.drafts[a],'A的新草稿');assert.equal(v.q.drafts[b],'B的新草稿')
    assert.equal(v.state.failedQuestion.text,'原始问题');assert.match(v.q.error,/另一解释线程/)
  }finally{d.resolve();v.close()}
})
