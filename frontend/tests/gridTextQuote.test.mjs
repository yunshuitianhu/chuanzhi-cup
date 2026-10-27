import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {createServer} from 'vite'
import {createSSRApp} from 'vue'
import {renderToString} from '@vue/server-renderer'
import {createPinia} from 'pinia'

globalThis.localStorage={getItem:()=>null,setItem:()=>{},removeItem:()=>{}}
const server=await createServer({configLoader:'runner',server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,entries:[]},appType:'custom',logLevel:'error',mode:'test'})
test.after(()=>server.close())
test('网格说明、流程卡及节点公式构成一个有明确来源的可引用区域',async()=>{
 const {default:Grid}=await server.ssrLoadModule('/src/components/WaterQualityGridTech.vue')
 const app=createSSRApp(Grid);app.use(createPinia())
 const html=await renderToString(app)
 assert.match(html,/id="grid_help"/)
 assert.match(html,/data-quote-region="text"/)
 assert.match(html,/data-quote-source="[^\"]*grid_help/)
 assert.match(html,/引用此段/)
 // 第一个按钮属于QuoteText，之前的正文就是该可见说明区域。
 const body=html.split('<button')[0]
 const plain=body.replace(/<!--[\s\S]*?-->/g,'').replace(/<\/(?:p|div|span|strong)>/g,'\n').replace(/<[^>]+>/g,'').replace(/\s+/g,' ').trim()
 const canonical=(await readFile(new URL('../../backend/grid_help.txt',import.meta.url),'utf8')).replace(/\s+/g,' ').trim()
 assert.equal(plain,canonical,'页面改字时必须同时更新服务端原文，避免出现菜单后核对失败')
 assert.match(html,/19\.86%/)
 assert.match(html,/不能作为现场检测准确率/)
})
