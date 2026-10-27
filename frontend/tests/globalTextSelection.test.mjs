import test from 'node:test'
import assert from 'node:assert/strict'
import {selectedPassage,quoteKey} from '../src/utils/textQuestions.js'
import {questionContextLocation} from '../src/utils/questionContext.js'
function el(parent=null,props={}){
 const x={nodeType:1,parentElement:parent,children:[],id:'',dataset:{},...props}
 if(parent)parent.children.push(x)
 x.getClientRects=()=>x.hidden?[]:[{}]
 x.contains=n=>{for(let p=n;p;p=p.parentElement)if(p===x)return true;return false}
 x.closest=selector=>{for(let p=x;p;p=p.parentElement){
  if(selector==='[data-quote-region]'&&p.quote)return p
  if(selector==='[id]'&&p.id)return p
  if(selector.startsWith('#app')&&p.app)return p
  if(selector.includes('input,textarea')&&p.excluded)return p
 }return null}
 x.querySelectorAll=()=>{const all=[];function walk(n){for(const c of n.children){if(c.excluded)all.push(c);walk(c)}}walk(x);return all}
 Object.defineProperty(x,'innerText',{get(){throw Error('Do not read whole page text')}})
 return x
}
function fixture(){
 const app=el(null,{app:true,id:'app'}),main=el(app,{id:'main-content'}),section=el(main,{id:'model-details'}),a=el(section),b=el(section)
 const range={commonAncestorContainer:section,intersectsNode:node=>!!node.intersected}
 const selection={rangeCount:1,isCollapsed:false,anchorNode:a,focusNode:b,toString:()=> '所选说明\n零值 0',getRangeAt:()=>range}
 return {app,main,section,a,b,selection,range}
}
for(const path of ['/chat','/charts','/river','/tech'])test(`${path} 普通正文可全局引用，仅读取选中文字和本地定位`,()=>{
 const f=fixture(),result=selectedPassage(f.selection,{path,title:'当前页'})
 assert.equal(result.text,'所选说明\n零值 0')
 assert.deepEqual(result.source,{kind:'page',path,anchor:'model-details'})
})
test('具体原文来源优先，跨区域选区明确回落为页面选文而非伪造某条原文',()=>{
 const f=fixture();f.section.quote=true;f.section.dataset={quoteSource:JSON.stringify({kind:'help',help_id:'grid_help'}),quoteTitle:'网格说明'}
 assert.equal(selectedPassage(f.selection,{path:'/tech'}).source.kind,'help')
 const second=el(f.main,{quote:true,id:'other',dataset:{quoteSource:JSON.stringify({kind:'help',help_id:'rag_help'})}})
 f.selection.focusNode=el(second);f.range.commonAncestorContainer=f.main
 assert.equal(selectedPassage(f.selection),null)
 assert.equal(selectedPassage(f.selection,{path:'/tech'}).source.kind,'page')
})
test('编辑框、隐藏区域、跨表单内容及应用外页面不可引用',()=>{
 const f=fixture();f.a.excluded=true;assert.equal(selectedPassage(f.selection,{path:'/tech'}),null)
 f.a.excluded=false;f.a.hidden=true;assert.equal(selectedPassage(f.selection,{path:'/tech'}),null)
 f.a.hidden=false;const input=el(f.section,{excluded:true,intersected:true})
 assert.equal(selectedPassage(f.selection,{path:'/tech'}),null)
 input.intersected=false;assert.ok(selectedPassage(f.selection,{path:'/tech'}))
 f.app.app=false;assert.equal(selectedPassage(f.selection,{path:'/tech'}),null)
})
test('不接受非应用地址，超长文本原样交给可见长度提示而不静默截断',()=>{
 const f=fixture();assert.equal(selectedPassage(f.selection,{path:'https://outside.test'}),null)
 f.selection.toString=()=> '字'.repeat(3001)
 assert.equal(selectedPassage(f.selection,{path:'/tech'}).text.length,3001)
})
test('页面引用标识含实际页面和锚点；返回来源不会执行外部链接',()=>{
 const base={text:'同文',project_id:'p',analysis_revision:1,results_revision:2,source:{kind:'page',path:'/tech',anchor:'model-details'}}
 assert.notEqual(quoteKey(base),quoteKey({...base,source:{...base.source,path:'/river'}}))
 const card={id:'a'.repeat(32),kind:'text_quote',title:'页面选文',summary:'未核验',notice:'待解释',scope:{project_id:'p',analysis_revision:1,results_revision:2,data_version:'b'.repeat(64)},selection:{text:'同文',source:base.source},source_url:'https://bad.example'}
 assert.deepEqual(questionContextLocation(card),{path:'/tech',query:{text_context:card.id},hash:'#model-details'})
 card.selection.source={kind:'page',path:'https://bad.example'};assert.equal(questionContextLocation(card),null)
 card.selection.source={kind:'help',help_id:'grid_help'};assert.equal(questionContextLocation(card).hash,'#grid_help')
})

test('页面切换后已经移除的DOM选区不能生成新页面引用',()=>{
 const f=fixture();f.a.isConnected=false
 assert.equal(selectedPassage(f.selection,{path:'/river'}),null)
})
