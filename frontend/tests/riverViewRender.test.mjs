// 渲染实际Vue组件，检查控件不再落进浏览器的惰性HTML template容器。
import test from 'node:test'
import assert from 'node:assert/strict'
import { createServer } from 'vite'
import { createSSRApp } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { createPinia } from 'pinia'

globalThis.localStorage = { getItem:()=>null, setItem:()=>{}, removeItem:()=>{} }
const server=await createServer({configLoader:'runner',server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,entries:[]},appType:'custom',logLevel:'error',mode:'test'})
test.after(async()=>{await server.close()})
const { default:RiverView }=await server.ssrLoadModule('/src/views/RiverView.vue')
test('无数据也显示手工建模、初始化、水动力和污染事件控件',async()=>{
  const app=createSSRApp(RiverView);app.use(createPinia())
  const html=await renderToString(app)
  assert.doesNotMatch(html,/<template(?:\s|>)/i)
  for(const label of ['河段名称','参考水面宽','初始化模型','运行水动力','模拟污染事件','排放开始 h','水质网格细分','4 倍（默认）']) {
    assert.ok(html.includes(label),`missing visible control: ${label}`)
  }
  assert.ok((html.match(/<input\b/g)||[]).length>=20)
  assert.match(html,/class="river-workspace"/)
})

test('独立网格明确区分水动力断面与细分水质节点',async()=>{
  const {default:Resolution}=await server.ssrLoadModule('/src/components/NumericalResolution.vue')
  const html=await renderToString(createSSRApp(Resolution,{diagnostics:{requested_dt_s:60,
    grid_nodes:397,hydro_grid_nodes:100,wq_refinement:4,min_spacing_m:15.15,max_spacing_m:15.15}}))
  assert.match(html,/水动力断面 100 个/)
  assert.match(html,/共 397 个计算节点/)
  assert.match(html,/未增加实测断面信息/)
})

test('数值分辨率区分请求步长与实际子步，并保留零值',async()=>{
  const {default:Resolution}=await server.ssrLoadModule('/src/components/NumericalResolution.vue')
  const html=await renderToString(createSSRApp(Resolution,{diagnostics:{requested_dt_s:60,
    actual_min_dt_s:.5,actual_max_dt_s:2,transport_substeps:420,grid_nodes:81,
    min_spacing_m:10,max_spacing_m:20,dispersion_min_m2s:0,dispersion_max_m2s:12.6}}))
  assert.match(html,/请求步长 60\.000 s/)
  assert.match(html,/实际输移子步 0\.500–2\.000 s/)
  assert.match(html,/0\.000–12\.600/)
  assert.match(html,/质量守恒不能代替精度验证/)
})

test('旧模拟没有分辨率时明确提示，不补造默认步长',async()=>{
  const {default:Resolution}=await server.ssrLoadModule('/src/components/NumericalResolution.vue')
  const html=await renderToString(createSSRApp(Resolution,{diagnostics:{}}))
  assert.match(html,/本结果未记录数值分辨率/)
  assert.doesNotMatch(html,/请求步长 60/)
})

test('任务进度提供停止按钮与中文工具阶段，结束后不再显示停止',async()=>{
  const {default:Progress}=await server.ssrLoadModule('/src/components/AgentProgress.vue')
  const run={run_id:'controlled',busy:true,status:'running',turn:1,tool_count:1,
    steps:[{id:'step',name:'find_black_spots',status:'running',elapsed_s:1.2}]}
  const running=await renderToString(createSSRApp(Progress,{run,elapsed:2.4}))
  assert.match(running,/停止本轮/);assert.match(running,/筛查异常点/);assert.match(running,/2秒/)
  const ended=await renderToString(createSSRApp(Progress,{run:{...run,busy:false,status:'cancelled',stop_requested:true,stage:'本轮已停止',
    steps:[{...run.steps[0],status:'cancelled'}]},elapsed:3}))
  assert.doesNotMatch(ended,/停止本轮/);assert.match(ended,/已停止/)
  assert.doesNotMatch(ended,/等待当前调用收尾|仍保持锁定/)
})

test('新回答提供证据入口，历史与未完成回答不补造入口',async()=>{
  const {default:Bubble}=await server.ssrLoadModule('/src/components/ChatBubble.vue')
  async function render(props) {
    const app=createSSRApp(Bubble,{role:'assistant',content:'氨氮均值为0。',...props});app.use(createPinia())
    return renderToString(app)
  }
  assert.match(await render({evidence:{id:'actual-bundle'}}),/查看本轮数据与工具证据/)
  assert.doesNotMatch(await render({}),/查看本轮数据与工具证据/)
  assert.doesNotMatch(await render({evidence:{id:'actual-bundle'},incomplete:true}),/查看本轮数据与工具证据/)
  assert.doesNotMatch(await render({role:'user',evidence:{id:'foreign'}}),/查看本轮数据与工具证据/)
})

test('工具和报告中的HTML仅显示为文本',async()=>{
  const {default:Bubble}=await server.ssrLoadModule('/src/components/ChatBubble.vue')
  const app=createSSRApp(Bubble,{role:'assistant',content:'<img src=x onerror="alert(1)">'})
  app.use(createPinia())
  const html=await renderToString(app)
  assert.doesNotMatch(html,/<img\b/)
  assert.match(html,/&lt;img/)
})
