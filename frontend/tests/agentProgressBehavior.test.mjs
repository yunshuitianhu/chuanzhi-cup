import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {parse,compileScript,compileTemplate} from '@vue/compiler-sfc'
import {createRenderer,nextTick,reactive,h} from 'vue'

const vueURL=import.meta.resolve('vue')
const source=await readFile(new URL('../src/components/AgentProgress.vue',import.meta.url),'utf8')
const {descriptor}=parse(source)
const script=compileScript(descriptor,{id:'agent-progress-review'})
const template=compileTemplate({source:descriptor.template.content,filename:'AgentProgress.vue',id:'agent-progress-review',compilerOptions:{bindingMetadata:script.bindings}})
if(template.errors.length) throw new Error(template.errors.join(' '))
const rewrite=code=>code.replace(/from ['"]([^'"]+)['"]/g,(_match,dep)=>'from '+JSON.stringify(dep==='vue'?vueURL:dep))
let serial=0
const importCode=async code=>import('data:text/javascript;base64,'+Buffer.from(rewrite(code)).toString('base64')+'#'+(++serial))
const {default:Component}=await importCode(script.content)
const templateModule=await importCode(template.code)
Component.render=templateModule.render
const settle=async()=>{await nextTick();await new Promise(resolve=>setImmediate(resolve));await nextTick()}
const makeRun=(overrides={})=>({run_id:'r1',status:'running',busy:true,turn:1,tool_count:0,elapsed_s:5,stage:'等待模型',steps:[],...overrides})

function createHost(){
  const createElement=tag=>({type:'element',tag,props:{},children:[],parentNode:null,listeners:{}})
  const createText=text=>({type:'text',text,parentNode:null})
  const createComment=text=>({type:'comment',text,parentNode:null})
  const remove=child=>{
    const parent=child?.parentNode
    if(!parent) return
    const index=parent.children.indexOf(child)
    if(index>=0) parent.children.splice(index,1)
    child.parentNode=null
  }
  const insert=(child,parent,anchor=null)=>{
    if(!child || !parent) return
    if(child.parentNode) remove(child)
    child.parentNode=parent
    const index=anchor ? parent.children.indexOf(anchor) : -1
    if(index>=0) parent.children.splice(index,0,child)
    else parent.children.push(child)
  }
  const parentNode=node=>node?.parentNode || null
  const nextSibling=node=>{
    const parent=node?.parentNode
    if(!parent) return null
    const index=parent.children.indexOf(node)
    return index>=0 ? parent.children[index+1] || null : null
  }
  const setText=(node,text)=>{ if(node) node.text=text }
  const setElementText=(node,text)=>{
    if(!node) return
    node.children.forEach(child=>{child.parentNode=null})
    node.children=[]
    if(text!=null){
      const textNode=createText(text)
      textNode.parentNode=node
      node.children.push(textNode)
    }
  }
  const patchProp=(el,key,prev,next)=>{
    if(/^on[A-Z]/.test(key)){
      const event=key.slice(2).toLowerCase()
      if(next){
        el.listeners[key]=next
        el.listeners[event]=next
      }else{
        delete el.listeners[key]
        delete el.listeners[event]
      }
      return
    }
    if(key==='class' || key==='style'){
      el.props[key]=next
      return
    }
    el.props[key]=next
  }
  return {createElement,createText,createComment,insert,remove,parentNode,nextSibling,setText,setElementText,patchProp}
}

async function mountAgent(initialRun,elapsed=12,listeners={}){
  const host=createHost()
  const props=reactive({run:reactive(initialRun),elapsed,...listeners})
  const renderer=createRenderer(host)
  const app=renderer.createApp({render:()=>h(Component,props)})
  const root=host.createElement('root')
  const parent=app.mount(root)
  const vm=parent.$.subTree.component.proxy
  await settle()
  return {app,props,root,vm,state:vm.$.setupState,async settle(){await settle()},close(){app.unmount()}}
}

const nodeText=node=>{
  if(!node) return ''
  if(node.type==='text') return String(node.text??'')
  return (node.children||[]).map(nodeText).join(' ')
}
const allNodes=(node,out=[])=>{
  if(node?.type==='element') out.push(node)
  for(const child of node?.children||[]) allNodes(child,out)
  return out
}
const classOf=node=>Array.isArray(node?.props?.class) ? node.props.class.join(' ') : String(node?.props?.class||'')
const byClass=(root,name)=>allNodes(root).find(node=>classOf(node).split(' ').filter(Boolean).includes(name))
const buttonByText=(root,pattern)=>allNodes(root).filter(node=>node.tag==='button').find(node=>pattern.test(nodeText(node)))

test('当前操作显示服务端步骤和等待模型/工具/回答状态',async()=>{
  const v=await mountAgent(makeRun({steps:[{id:'catalog',name:'get_analysis_card_catalog',status:'running',elapsed_s:1.1}]}),12)
  try{
    assert.match(nodeText(v.root),/正在执行：读取分析卡片目录/)
    assert.match(nodeText(byClass(v.root,'latest')),/读取分析卡片目录/)
    assert.match(nodeText(v.root),/已用 12秒/)
    assert.doesNotMatch(nodeText(v.root),/%/)
    v.props.run.steps=[]
    v.props.run.stage='等待模型返回'
    await v.settle()
    assert.match(nodeText(v.root),/等待模型返回/)
    v.props.run.stage='执行工具：读取监测摘要'
    await v.settle()
    assert.match(nodeText(v.root),/等待工具完成/)
    v.props.run.stage='生成最终回答'
    await v.settle()
    assert.match(nodeText(v.root),/正在整理回答/)
    v.props.run.stop_requested=true
    await v.settle()
    assert.match(nodeText(v.root),/停止请求已收到/)
  }finally{v.close()}
})

test('停止和核对任务事件语义保持不变',async()=>{
  const calls=[]
  const v=await mountAgent(makeRun({steps:[]}),5,{onStop:()=>calls.push('stop'),onSync:()=>calls.push('sync')})
  try{
    buttonByText(v.root,/停止本轮/).listeners.onClick({})
    buttonByText(v.root,/核对任务状态/).listeners.onClick({})
    await v.settle()
    assert.deepEqual(calls,['stop','sync'])
  }finally{v.close()}
})

test('历史默认跟随最新；用户上滚暂停并在新步骤保持位置，最新按钮恢复',async()=>{
  const steps=Array.from({length:20},(_,i)=>({id:'s'+i,name:'get_data_summary',status:'succeeded',elapsed_s:0.1}))
  const v=await mountAgent(makeRun({steps,tool_count:20}),7)
  try{
    const history=byClass(v.root,'history')
    assert.ok(history)
    assert.equal(typeof history.listeners.onScroll,'function')
    history.scrollHeight=600
    history.clientHeight=180
    history.scrollTop=0
    history.listeners.onScroll()
    await v.settle()
    assert.equal(v.state.autoFollow,false)
    assert.equal(v.state.showLatest,true)
    v.props.run.steps.push({id:'s20',name:'submit_analysis_cards',status:'running',elapsed_s:0.2})
    await v.settle()
    assert.equal(history.scrollTop,0)
    assert.match(nodeText(byClass(v.root,'latest')),/提交分析卡片/)
    const latestButton=buttonByText(v.root,/最新步骤/)
    assert.ok(latestButton)
    assert.equal(typeof latestButton.listeners.onClick,'function')
    latestButton.listeners.onClick({})
    await v.settle()
    assert.equal(v.state.autoFollow,true)
    assert.equal(history.scrollTop,420)
    v.props.run.steps.push({id:'s21',name:'analyze_video',status:'running',elapsed_s:0.1})
    await v.settle()
    assert.equal(history.scrollTop,420)
    assert.equal(v.state.showLatest,false)
  }finally{v.close()}
})

test('历史键盘 End/Home 控制跟随',async()=>{
  const v=await mountAgent(makeRun({steps:[{id:'a',name:'get_data_summary',status:'running',elapsed_s:0.1}]}),2)
  try{
    const history=byClass(v.root,'history')
    assert.equal(typeof history.listeners.onKeydown,'function')
    history.scrollHeight=600
    history.clientHeight=180
    history.scrollTop=100
    history.listeners.onKeydown({key:'End',preventDefault(){}})
    await v.settle()
    assert.equal(history.scrollTop,420)
    assert.equal(v.state.autoFollow,true)
    history.listeners.onKeydown({key:'Home',preventDefault(){}})
    await v.settle()
    assert.equal(history.scrollTop,0)
    assert.equal(v.state.autoFollow,false)
    assert.equal(v.state.showLatest,true)
  }finally{v.close()}
})

test('选择文本时不自动跟随',async()=>{
  const v=await mountAgent(makeRun({steps:[{id:'a',name:'get_data_summary',status:'running',elapsed_s:0.1}]}),3)
  const had=Object.prototype.hasOwnProperty.call(globalThis,'getSelection')
  const previous=globalThis.getSelection
  try{
    const history=byClass(v.root,'history')
    history.scrollHeight=600
    history.clientHeight=180
    history.scrollTop=420
    globalThis.getSelection=()=>({rangeCount:1,isCollapsed:false})
    history.listeners.onScroll()
    await v.settle()
    assert.equal(v.state.autoFollow,false)
    assert.equal(v.state.showLatest,true)
  }finally{
    if(had) globalThis.getSelection=previous
    else delete globalThis.getSelection
    v.close()
  }
})

test('新 run 重置自动跟随并回到历史底部',async()=>{
  const v=await mountAgent(makeRun({steps:[{id:'a',name:'get_data_summary',status:'running',elapsed_s:0.1}]}),5)
  try{
    const history=byClass(v.root,'history')
    history.scrollHeight=500
    history.clientHeight=180
    history.scrollTop=10
    history.listeners.onScroll()
    await v.settle()
    assert.equal(v.state.autoFollow,false)
    v.props.run=reactive(makeRun({run_id:'r2',steps:[{id:'b',name:'find_black_spots',status:'running',elapsed_s:0.1}]}))
    await v.settle()
    const nextHistory=byClass(v.root,'history')
    assert.equal(v.state.autoFollow,true)
    assert.equal(v.state.showLatest,false)
    assert.equal(nextHistory.scrollTop,320)
  }finally{v.close()}
})

test('完成/部分/跳过/失败计数诚实，失败历史默认可见且不展示原始原因',async()=>{
  const v=await mountAgent(makeRun({
    run_id:'counts',
    status:'partial',
    busy:false,
    turn:2,
    tool_count:4,
    stage:'部分完成',
    steps:[
      {id:'1',name:'get_data_summary',status:'succeeded',elapsed_s:0.4},
      {id:'2',name:'find_black_spots',status:'failed',elapsed_s:1.2,reason:'RAW_CHAIN_OF_THOUGHT_SHOULD_NOT_RENDER'},
      {id:'3',name:'analyze_video',status:'skipped',elapsed_s:0},
      {id:'4',name:'cross_validate_video',status:'succeeded',elapsed_s:2}
    ]
  }),9)
  try{
    const text=nodeText(v.root)
    assert.match(text,/已完成 2/)
    assert.match(text,/失败 1/)
    assert.match(text,/已跳过 1/)
    assert.match(text,/部分完成/)
    assert.doesNotMatch(text,/成功/)
    assert.doesNotMatch(text,/RAW_CHAIN_OF_THOUGHT/)
    const history=byClass(v.root,'history')
    assert.ok(history)
    assert.match(nodeText(history),/筛查异常点/)
    assert.match(nodeText(history),/该步骤未完成/)
    assert.doesNotMatch(nodeText(history),/读取监测摘要/)
  }finally{v.close()}
})

test('任一步成功不代表整轮成功，步骤 notice 可选且有界',async()=>{
  const v=await mountAgent(makeRun({
    run_id:'warn',
    status:'succeeded',
    busy:false,
    steps:[
      {id:'ok',name:'get_data_summary',status:'succeeded',elapsed_s:1},
      {id:'bad',name:'submit_analysis_cards',status:'failed',elapsed_s:1,reason:'RAW_REASON'}
    ]
  }),10)
  try{
    let text=nodeText(v.root)
    assert.match(text,/已完成/)
    assert.match(text,/存在失败步骤/)
    assert.match(text,/提交分析卡片/)
    assert.doesNotMatch(text,/RAW_REASON/)
    assert.doesNotMatch(text,/undefined/)
    const history=byClass(v.root,'history')
    assert.match(nodeText(history),/提交分析卡片/)
    assert.doesNotMatch(nodeText(history),/读取监测摘要/)
    v.props.run.steps[1].notice='长'.repeat(200)
    await v.settle()
    text=nodeText(v.root)
    assert.match(text,/长{96}…/)
    assert.doesNotMatch(text,/长{97}/)
  }finally{v.close()}
})

test('完成时保留用户上滚阅读的位置和展开的历史',async()=>{
 const v=await mountAgent(makeRun({steps:[{id:'a',name:'analyze_video',status:'running'}]}))
 try{
  const history=byClass(v.root,'history'); history.scrollHeight=600;history.clientHeight=180;history.scrollTop=10
  history.listeners.onScroll();v.props.run.status='succeeded';v.props.run.busy=false
  await v.settle();assert.equal(v.state.historyOpen,true);assert.equal(history.scrollTop,10)
 }finally{v.close()}
})
test('Shift+Home保留原生文字选择，不改变跟随状态',async()=>{
 const v=await mountAgent(makeRun({steps:[{id:'a',name:'get_data_summary',status:'running'}]}))
 try{
  const history=byClass(v.root,'history');let prevented=false
  history.listeners.onKeydown({key:'Home',shiftKey:true,preventDefault(){prevented=true}})
  assert.equal(prevented,false);assert.equal(v.state.autoFollow,true)
 }finally{v.close()}
})
