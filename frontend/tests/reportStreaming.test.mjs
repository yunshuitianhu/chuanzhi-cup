import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { parse, compileScript } from '@vue/compiler-sfc'
import { createRenderer, h, nextTick, ref } from 'vue'

// Mount the real client component on an in-memory host. No browser DOM shim,
// source rewriting of its logic, external network or model request is needed.
const file=new URL('../src/components/SafeMarkdown.vue',import.meta.url)
const {descriptor}=parse(await readFile(file,'utf8'))
const script=compileScript(descriptor,{id:'report-stream-test',inlineTemplate:true})
const moduleCode=script.content.replaceAll("from 'vue'",`from ${JSON.stringify(import.meta.resolve('vue'))}`)
  .replaceAll('from "vue"',`from ${JSON.stringify(import.meta.resolve('vue'))}`)
  .replace("from '../utils/reportMarkdown'",`from ${JSON.stringify(new URL('../src/utils/reportMarkdown.js',import.meta.url).href)}`)
const {default:SafeMarkdown}=await import(`data:text/javascript;base64,${Buffer.from(moduleCode).toString('base64')}`)

const renderer=createRenderer({
  createElement:tag=>({tag,props:{},children:[]}),createText:text=>({text}),createComment:text=>({text}),
  insert(node,parent,anchor=null){node.parent=parent;const index=anchor?parent.children.indexOf(anchor):-1;if(index<0)parent.children.push(node);else parent.children.splice(index,0,node)},
  remove(node){if(node.parent)node.parent.children.splice(node.parent.children.indexOf(node),1)},
  setText(node,text){node.text=text},setElementText(node,text){node.text=text},
  parentNode:node=>node.parent,nextSibling:()=>null,patchProp(node,key,_old,value){node.props[key]=value},
})
function mount(){
  const content=ref(''),events=[],host={children:[]}
  const app=renderer.createApp({render:()=>h(SafeMarkdown,{content:content.value,onRendered:()=>events.push('rendered')})})
  app.mount(host)
  return {content,events,app,html:()=>host.children[0].props.innerHTML}
}

test('真实排版组件合并流式碎片后保留最后字符、零值及转义HTML',async t=>{
  t.mock.timers.enable({apis:['setTimeout']})
  const view=mount()
  try {
    for(const delta of ['## 筛查\n\n','DO=','0\n\n','<img src=x onerror=alert(1)>','最后一条记录']) {
      view.content.value+=delta;await nextTick()
    }
    assert.equal(view.events.length,0)
    t.mock.timers.tick(100);await nextTick();await nextTick()
    assert.match(view.html(),/<h2>筛查<\/h2>/);assert.match(view.html(),/DO=0/)
    assert.match(view.html(),/最后一条记录/);assert.match(view.html(),/&lt;img/)
    assert.doesNotMatch(view.html(),/<img\b/);assert.equal(view.events.length,1)
    view.content.value+='\n\n更新后的结论';await nextTick()
    t.mock.timers.tick(100);await nextTick();await nextTick()
    assert.match(view.html(),/更新后的结论/);assert.equal(view.events.length,2)
  } finally {view.app.unmount()}
})

test('切换项目卸载组件后，待刷新片段不再发出渲染完成通知',async t=>{
  t.mock.timers.enable({apis:['setTimeout']})
  const view=mount()
  view.content.value='旧项目的延迟片段';await nextTick()
  view.app.unmount();t.mock.timers.tick(1000);await nextTick()
  assert.deepEqual(view.events,[])
})
