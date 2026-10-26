import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {parse,compileScript} from '@vue/compiler-sfc'
import {createRenderer,nextTick,reactive} from 'vue'
import {textQuestionState} from '../src/utils/textQuestions.js'

const vueURL=import.meta.resolve('vue')
async function compile(file,id,dependency){
  const {descriptor}=parse(await readFile(new URL(file,import.meta.url),'utf8'))
  const code=compileScript(descriptor,{id,inlineTemplate:true}).content
    .replace(/from ['"]([^'"]+)['"]/g,(_m,dep)=>'from '+JSON.stringify(dep==='vue'?vueURL:dependency(dep)))
  return 'data:text/javascript;base64,'+Buffer.from(code).toString('base64')
}
const markdownURL=await compile('../src/components/SafeMarkdown.vue','side-render-markdown',
  ()=>new URL('../src/utils/reportMarkdown.js',import.meta.url).href)
const fixture='data:text/javascript,'+encodeURIComponent(`
export const useRouter=()=>({push:async()=>{}});
export const useSessionStore=()=>globalThis.__sideRenderStore;
export const api={};export const sseHeaders=()=>({});export const adoptSessionFrom=()=>{};
export const consumeAgentStream=async()=>{};export default {render:()=>null};`)
const componentURL=await compile('../src/components/SideQuestions.vue','side-render-review',dep=>
  dep==='./SafeMarkdown.vue'?markdownURL:dep==='../utils/textQuestions'?
    new URL('../src/utils/textQuestions.js',import.meta.url).href:fixture)
const {default:SideQuestions}=await import(componentURL)

test('切换解释线程立即显示对应回答，不把上一线程的延迟排版挂在新引用下',async t=>{
  t.mock.timers.enable({apis:['setTimeout']})
  const body={children:[]},root={children:[]}
  const renderer=createRenderer({
    createElement:tag=>({tag,tagName:tag.toUpperCase(),props:{},children:[],style:{},options:[],
      addEventListener(){},removeEventListener(){}}),createText:text=>({text}),createComment:text=>({text}),
    insert(node,parent,anchor=null){if(node.parent){const old=node.parent.children.indexOf(node);if(old>=0)node.parent.children.splice(old,1)}
      node.parent=parent;const i=anchor?parent.children.indexOf(anchor):-1;
      if(i<0)parent.children.push(node);else parent.children.splice(i,0,node)},
    remove(node){if(node?.parent)node.parent.children.splice(node.parent.children.indexOf(node),1)},
    setText(node,text){node.text=text},setElementText(node,text){node.text=text},
    parentNode:node=>node.parent,nextSibling:node=>node.parent?.children[node.parent.children.indexOf(node)+1]||null,
    patchProp(node,key,_old,value){node.props[key]=value},
    querySelector:()=>body,
  })
  const q=reactive(textQuestionState())
  q.open=true;q.active='A';q.threads=['A','B'].map(id=>({id,quote:{title:id},
    messages:[{role:'user',content:id+'的问题'},{role:'assistant',content:id+'的独立回答'}]}))
  const saved={document:globalThis.document,matchMedia:globalThis.matchMedia}
  globalThis.document={body:{style:{overflow:''}},querySelector:()=>null,activeElement:null}
  globalThis.matchMedia=()=>({matches:false,onchange:null})
  globalThis.__sideRenderStore=reactive({textQuestions:q,ready:true,workspaceRevision:1,
    sideRunning:false,loadSideThreads:async()=>{}})
  const app=renderer.createApp(SideQuestions)
  const html=()=>{const visit=node=>[node.props?.innerHTML||'',...(node.children||[]).map(visit)].join('');return visit(body)}
  try{
    app.mount(root);await nextTick();assert.match(html(),/A的独立回答/)
    q.active='B';await nextTick()
    assert.match(html(),/B的独立回答/);assert.doesNotMatch(html(),/A的独立回答/)
    q.active='A';await nextTick()
    assert.match(html(),/A的独立回答/);assert.doesNotMatch(html(),/B的独立回答/)
  }finally{app.unmount();Object.assign(globalThis,saved);delete globalThis.__sideRenderStore}
})
