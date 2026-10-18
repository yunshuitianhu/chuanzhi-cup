// 渲染实际Vue组件，检查控件不再落进浏览器的惰性HTML template容器。
import test from 'node:test'
import assert from 'node:assert/strict'
import { createServer } from 'vite'
import { createSSRApp } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { createPinia } from 'pinia'

globalThis.localStorage = { getItem:()=>null, setItem:()=>{}, removeItem:()=>{} }
const server=await createServer({server:{middlewareMode:true},appType:'custom',logLevel:'error',mode:'test'})
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
