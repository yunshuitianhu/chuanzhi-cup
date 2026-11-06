import test from 'node:test'
import assert from 'node:assert/strict'
import {fileURLToPath} from 'node:url'
import {createServer} from 'vite'
import {createSSRApp} from 'vue'
import {renderToString} from '@vue/server-renderer'
import {createPinia} from 'pinia'
import {createRouter,createMemoryHistory} from 'vue-router'
const server=await createServer({configLoader:'runner',server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,entries:[]},appType:'custom',logLevel:'error',mode:'test',resolve:{alias:[{find:'leaflet/dist/leaflet.css',replacement:fileURLToPath(new URL('./fixtures/mapLayerStub.js',import.meta.url))},{find:/^leaflet$/,replacement:fileURLToPath(new URL('./fixtures/mapLayerStub.js',import.meta.url))}]}})
test.after(()=>server.close())
test('无Key的AI界面明确降级并禁用发起按钮，不产生伪回答',async()=>{
 globalThis.localStorage={getItem:()=>null,setItem:()=>{},removeItem:()=>{}}
 globalThis.requestAnimationFrame=()=>0
 globalThis.cancelAnimationFrame=()=>{}
 const {default:C}=await server.ssrLoadModule('/src/components/simple/SimpleAIInsight.vue')
 const app=createSSRApp(C);app.use(createPinia())
 const router=createRouter({history:createMemoryHistory(),routes:[{path:'/simple',component:{render:()=>null}}]})
 await router.push('/simple');app.use(router)
 const html=await renderToString(app)
 assert.match(html,/基础检测可用/);assert.match(html,/disabled/);assert.match(html,/确认发送/)
})

test('简易表格保留0与缺测，转义导入文本',async()=>{
 const {default:C}=await server.ssrLoadModule('/src/components/simple/SimpleDataTable.vue')
 const {useSessionStore}=await server.ssrLoadModule('/src/stores/session.js')
 const pinia=createPinia(),store=useSessionStore(pinia)
 store.preview={columns:['ammonia_nitrogen','ph','datetime'],rows:[{ammonia_nitrogen:0,ph:null,datetime:'<unsafe>'}],total:1}
 const app=createSSRApp(C);app.use(pinia)
 const html=await renderToString(app)
 assert.match(html,/>0<\/td>/);assert.match(html,/缺测/);assert.match(html,/&lt;unsafe&gt;/)
})
test('进入建议页不创建任务，明确人工确认与未实施身份',async()=>{
 const {default:C}=await server.ssrLoadModule('/src/components/simple/SimpleNextActions.vue')
 const app=createSSRApp(C);app.use(createPinia());app.component('RouterLink',{template:'<a><slot /></a>'})
 const html=await renderToString(app)
 assert.match(html,/显式保存后/);assert.match(html,/并非 AI 生成结论/);assert.doesNotMatch(html,/class="investigation-board"/)
})
