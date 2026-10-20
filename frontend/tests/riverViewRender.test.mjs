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
  for(const label of ['河段名称','参考水面宽','初始化模型','运行水动力','模拟污染事件','排放开始 h']) {
    assert.ok(html.includes(label),`missing visible control: ${label}`)
  }
  assert.ok((html.match(/<input\b/g)||[]).length>=20)
  assert.match(html,/class="river-workspace"/)
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
