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
async function openDetails(v){
  const toggle=buttonByText(v.root,/查看详情/)
  assert.ok(toggle,'应提供查看详情按钮')
  toggle.listeners.onClick({})
  await v.settle()
  assert.equal(v.state.detailsOpen,true)
}

test('运行中默认只显示紧凑状态，当前工具、耗时与详情入口可见',async()=>{
  const v=await mountAgent(makeRun({steps:[{id:'catalog',name:'get_analysis_card_catalog',status:'running',elapsed_s:1.1}]}),12)
  try{
    assert.match(nodeText(v.root),/正在执行：读取分析卡片目录/)
    assert.match(nodeText(v.root),/已用 12秒/)
    assert.match(nodeText(v.root),/进行中 1/)
    assert.doesNotMatch(nodeText(v.root),/%/)
    assert.equal(v.state.detailsOpen,false)
    assert.equal(byClass(v.root,'history'),undefined)
    await openDetails(v)
    assert.match(nodeText(byClass(v.root,'latest')),/读取分析卡片目录/)
    assert.equal(v.state.detailsOpen,true)
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
    assert.equal(v.state.detailsOpen,false)
    await openDetails(v)
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
    await openDetails(v)
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
    await openDetails(v)
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

test('新 run 重置自动跟随并在打开详情后回到历史底部',async()=>{
  const v=await mountAgent(makeRun({steps:[{id:'a',name:'get_data_summary',status:'running',elapsed_s:0.1}]}),5)
  try{
    await openDetails(v)
    const history=byClass(v.root,'history')
    history.scrollHeight=500
    history.clientHeight=180
    history.scrollTop=10
    history.listeners.onScroll()
    await v.settle()
    assert.equal(v.state.autoFollow,false)
    v.props.run=reactive(makeRun({run_id:'r2',steps:[{id:'b',name:'find_black_spots',status:'running',elapsed_s:0.1}]}))
    await v.settle()
    assert.equal(v.state.detailsOpen,false)
    assert.equal(v.state.autoFollow,true)
    assert.equal(v.state.showLatest,false)
    await openDetails(v)
    const nextHistory=byClass(v.root,'history')
    nextHistory.scrollHeight=500
    nextHistory.clientHeight=180
    v.state.scrollToLatest()
    assert.equal(nextHistory.scrollTop,320)
  }finally{v.close()}
})

test('结束后默认收起详情；尝试级计数与失败原因只在详情中展开且不展示原始原因',async()=>{
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
    let text=nodeText(v.root)
    assert.match(text,/部分完成/)
    assert.match(text,/未恢复失败 1/)
    assert.match(text,/4 次工具调用/)
    assert.doesNotMatch(text,/已完成 2/)
    assert.doesNotMatch(text,/成功/)
    assert.doesNotMatch(text,/RAW_CHAIN_OF_THOUGHT/)
    assert.equal(v.state.detailsOpen,false)
    assert.equal(byClass(v.root,'history'),undefined)
    await openDetails(v)
    text=nodeText(v.root)
    assert.match(text,/已完成 2/)
    assert.match(text,/失败 1/)
    assert.match(text,/已跳过 1/)
    assert.match(text,/未恢复失败 1/)
    assert.doesNotMatch(text,/成功/)
    assert.doesNotMatch(text,/RAW_CHAIN_OF_THOUGHT/)
    const history=byClass(v.root,'history')
    assert.ok(history)
    assert.match(nodeText(history),/筛查异常点/)
    assert.match(nodeText(history),/该步骤未完成/)
    assert.match(nodeText(history),/读取监测摘要/)
    assert.match(nodeText(history),/核对视频与监测记录/)
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
    assert.match(text,/未恢复失败 1/)
    assert.doesNotMatch(text,/RAW_REASON/)
    assert.doesNotMatch(text,/undefined/)
    assert.equal(v.state.detailsOpen,false)
    await openDetails(v)
    text=nodeText(v.root)
    assert.match(text,/提交分析卡片/)
    assert.match(text,/读取监测摘要/)
    assert.doesNotMatch(text,/RAW_REASON/)
    v.props.run.steps[1].notice='长'.repeat(200)
    await v.settle()
    text=nodeText(v.root)
    assert.match(text,/长{96}…/)
    assert.doesNotMatch(text,/长{97}/)
  }finally{v.close()}
})

test('完成时保留用户展开的详情和上滚阅读位置',async()=>{
 const v=await mountAgent(makeRun({steps:[{id:'a',name:'analyze_video',status:'running'}]}))
 try{
  await openDetails(v)
  const history=byClass(v.root,'history'); history.scrollHeight=600;history.clientHeight=180;history.scrollTop=10
  history.listeners.onScroll();v.props.run.status='succeeded';v.props.run.busy=false
  await v.settle();assert.equal(v.state.detailsOpen,true);assert.equal(history.scrollTop,10)
 }finally{v.close()}
})
test('Shift+Home保留原生文字选择，不改变跟随状态',async()=>{
 const v=await mountAgent(makeRun({steps:[{id:'a',name:'get_data_summary',status:'running'}]}))
 try{
  await openDetails(v)
  const history=byClass(v.root,'history');let prevented=false
  history.listeners.onKeydown({key:'Home',shiftKey:true,preventDefault(){prevented=true}})
  assert.equal(prevented,false);assert.equal(v.state.autoFollow,true)
 }finally{v.close()}
})

test('报告首次失败后重试成功：紧凑摘要说明已恢复，详情保留两次尝试',async()=>{
  const v=await mountAgent(makeRun({
    run_id:'report-recovered',auto:true,status:'succeeded',busy:false,turn:8,tool_count:19,
    steps:[
      {id:'r1',name:'generate_final_report',operation_id:'report-op',recovered_by:'r2',status:'failed',elapsed_s:0,notice:'输入、参数或前置步骤不满足要求；请核对数据和相应页面的配置。'},
      {id:'r2',name:'generate_final_report',operation_id:'report-op',retry_of:'r1',status:'succeeded',elapsed_s:11.8}
    ]
  }),106)
  try{
    let text=nodeText(v.root)
    assert.match(text,/本轮已完成/)
    assert.match(text,/报告已生成/)
    assert.match(text,/1次报告失败后重试成功/)
    assert.match(text,/19 次工具调用/)
    assert.doesNotMatch(text,/未恢复失败/)
    assert.equal(v.state.detailsOpen,false)
    assert.equal(byClass(v.root,'history'),undefined)
    await openDetails(v)
    text=nodeText(v.root)
    assert.match(text,/报告状态/)
    assert.match(text,/第 1 次/)
    assert.match(text,/第 2 次/)
    assert.match(text,/后续报告调用已成功/)
    const history=byClass(v.root,'history')
    assert.match(nodeText(history),/第1次/)
    assert.match(nodeText(history),/第2次/)
    assert.match(nodeText(history),/生成分析报告/)
    assert.match(nodeText(history),/11.8秒/)
    v.state.onDetailsKeydown({key:'Escape',preventDefault(){}})
    await v.settle()
    assert.equal(v.state.detailsOpen,false)
  }finally{v.close()}
})

test('最新一次报告失败时不沿用旧成功，紧凑摘要保持未完成',async()=>{
  const v=await mountAgent(makeRun({
    run_id:'report-stale',auto:true,status:'partial',busy:false,turn:9,tool_count:18,
    steps:[
      {id:'r1',name:'generate_final_report',status:'succeeded',elapsed_s:11.8},
      {id:'r2',name:'generate_final_report',status:'failed',elapsed_s:0,notice:'输入、参数或前置步骤不满足要求。'}
    ]
  }),80)
  try{
    const text=nodeText(v.root)
    assert.match(text,/部分完成/)
    assert.match(text,/报告未更新（最新一次失败）/)
    assert.match(text,/未恢复失败 1/)
    assert.doesNotMatch(text,/报告已生成/)
  }finally{v.close()}
})

test('同名非报告工具没有重试关联时仍保留未恢复失败',async()=>{
  const v=await mountAgent(makeRun({
    run_id:'same-name',status:'succeeded',busy:false,turn:3,tool_count:2,
    steps:[
      {id:'g1',name:'reverse_geocode',status:'failed',elapsed_s:0.2},
      {id:'g2',name:'reverse_geocode',status:'succeeded',elapsed_s:0.3}
    ]
  }),12)
  try{
    let text=nodeText(v.root)
    assert.doesNotMatch(text,/失败后同名步骤已成功/)
    assert.match(text,/未恢复失败 1/)
    assert.doesNotMatch(text,/报告已生成/)
    await openDetails(v)
    text=nodeText(v.root)
    assert.doesNotMatch(text,/后续同名调用已成功/)
    assert.doesNotMatch(text,/后续报告调用已成功/)
  }finally{v.close()}
})

test('未调用报告工具时明确显示未生成，跳过的视频步骤只在详情说明',async()=>{
  const v=await mountAgent(makeRun({
    run_id:'no-report',auto:true,status:'succeeded',busy:false,turn:4,tool_count:1,
    steps:[{id:'v1',name:'analyze_video',status:'skipped',elapsed_s:0,notice:'未上传视频，此步骤不适用；监测表分析可继续。'}]
  }),9)
  try{
    let text=nodeText(v.root)
    assert.match(text,/未生成报告/)
    assert.match(text,/1 次工具调用/)
    assert.doesNotMatch(text,/未上传视频/)
    await openDetails(v)
    text=nodeText(v.root)
    assert.match(text,/已跳过 1/)
    assert.match(text,/未上传视频/)
  }finally{v.close()}
})

test('仅有连接错误时不显示工具统计和详情入口',async()=>{
  const v=await mountAgent({run_id:null,status:'idle',busy:false,steps:[]},0,{connectionError:'暂时无法核对任务状态，请恢复连接后重试。'})
  try{
    const text=nodeText(v.root)
    assert.match(text,/暂时无法核对任务状态/)
    assert.doesNotMatch(text,/0 次工具调用/)
    assert.doesNotMatch(text,/已用 0秒/)
    assert.equal(buttonByText(v.root,/查看详情/),undefined)
    assert.ok(buttonByText(v.root,/核对任务状态/))
  }finally{v.close()}
})

test('工具总数超过保留步骤时明确标注最近尝试，不把100条当成全部',async()=>{
  const steps=Array.from({length:100},(_,i)=>({id:'t'+i,name:'get_data_summary',status:'succeeded',elapsed_s:0.1}))
  const v=await mountAgent(makeRun({run_id:'truncated',status:'succeeded',busy:false,tool_count:120,steps}),5)
  try{
    assert.match(nodeText(v.root),/查看详情（最近100次）/)
    assert.match(nodeText(v.root),/120 次工具调用/)
    await openDetails(v)
    assert.match(nodeText(v.root),/最近工具尝试（100\s*\/\s*120\s*）/)
    assert.match(nodeText(v.root),/服务端只保留最近 100 次步骤/)
  }finally{v.close()}
})


test('不同坐标查询不会消除原失败，即使名称相同',async()=>{
 const v=await mountAgent(makeRun({status:'partial',busy:false,steps:[
  {id:'A',name:'reverse_geocode',status:'failed',operation_id:'query-A'},
  {id:'B',name:'reverse_geocode',status:'succeeded',operation_id:'query-B'}]}))
 try{assert.equal(v.state.unresolvedFailures.length,1);assert.equal(v.state.recoveredFailures.length,0)}finally{v.close()}
})
test('恢复必须同时具有服务端关联和同一操作标识',async()=>{
 const v=await mountAgent(makeRun({status:'succeeded',busy:false,steps:[
  {id:'A',name:'reverse_geocode',status:'failed',operation_id:'query-A',recovered_by:'B'},
  {id:'B',name:'reverse_geocode',status:'succeeded',operation_id:'query-B'}]}))
 try{assert.equal(v.state.unresolvedFailures.length,1);v.props.run.steps[1].operation_id='query-A';await v.settle();assert.equal(v.state.unresolvedFailures.length,0);assert.equal(v.state.recoveredFailures.length,1)}finally{v.close()}
})
test('截断历史未出现报告时显示未知，不断言未生成',async()=>{
 const v=await mountAgent(makeRun({auto:true,status:'succeeded',busy:false,tool_count:120,steps:Array.from({length:100},(_,i)=>({id:'tail'+i,name:'get_data_summary',status:'succeeded'}))}))
 try{assert.match(nodeText(v.root),/报告状态暂无法核实/);assert.doesNotMatch(nodeText(v.root),/未生成报告/)}finally{v.close()}
})
test('被截断的报告调用仍可从本轮服务端报告状态核实',async()=>{
 const v=await mountAgent(makeRun({auto:false,analysis_revision:2,status:'succeeded',busy:false,tool_count:120,
  report_result:{step_id:'older-report',status:'succeeded',attempts:1,analysis_revision:2,results_revision:3},
  steps:Array.from({length:100},(_,i)=>({id:'tail'+i,name:'get_data_summary',status:'succeeded'}))}))
 try{assert.match(nodeText(v.root),/报告已生成/);await openDetails(v);assert.match(nodeText(v.root),/状态来自服务端本轮记录/)}finally{v.close()}
})
test('报告状态属于不同分析版本时不作为本轮完成依据',async()=>{
 const v=await mountAgent(makeRun({auto:true,analysis_revision:3,status:'partial',busy:false,tool_count:120,
  report_result:{step_id:'older-report',status:'succeeded',attempts:1,analysis_revision:2,results_revision:3},
  steps:[{id:'tail',name:'get_data_summary',status:'succeeded'}]}))
 try{assert.match(nodeText(v.root),/暂无法核实/);assert.doesNotMatch(nodeText(v.root),/报告已生成/)}finally{v.close()}
})
