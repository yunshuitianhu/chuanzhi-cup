import test from 'node:test'
import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'
import { createSSRApp, h } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { createPinia } from 'pinia'

const server=await createServer({
  server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,entries:[]},appType:'custom',logLevel:'error',mode:'test',
  resolve:{alias:[
    {find:'leaflet/dist/leaflet.css',replacement:fileURLToPath(new URL('./fixtures/mapLayerStub.js',import.meta.url))},
    {find:/^leaflet$/,replacement:fileURLToPath(new URL('./fixtures/mapLayerStub.js',import.meta.url))},
  ]},
})
test.after(async()=>{await server.close()})
const {amapStaticLayer}=await server.ssrLoadModule('/src/utils/amapStaticLayer.js')
const {images,point}=await server.ssrLoadModule('/tests/fixtures/mapLayerStub.js')
function fakeMap() {
  return {handlers:new Set(),getSize:()=>({x:1400,y:460}),getZoom:()=>15,
    getCenter:()=>({lat:23.12345678,lng:113.12345678}),project:([lat,lon])=>point(lon*100,lat*100),
    unproject:p=>[p.y/100,p.x/100],on(event,fn){this.handlers.add(fn)},off(event,fn){this.handlers.delete(fn)},
  }
}
function start(layer,map) {layer.onAdd(map);clearTimeout(layer.timer)}
const tick=()=>new Promise(resolve=>setImmediate(resolve))

test('视口取图按实际请求中心定位，换图前保留旧图，卸载释放资源',async(t)=>{
  images.length=0
  const revoked=[]
  t.mock.method(URL,'createObjectURL',()=>`blob:test-${images.length}`)
  t.mock.method(URL,'revokeObjectURL',u=>revoked.push(u))
  const calls=[],states=[],map=fakeMap()
  const layer=amapStaticLayer(async params=>{calls.push(params);return new Blob(['image'])},(...s)=>states.push(s))
  start(layer,map)
  const first=layer.refresh();await tick()
  assert.deepEqual(calls[0],{lat:23.123457,lon:113.123457,zoom:14,width:700,height:230})
  assert.equal(images[0].opacity,0)
  images[0].emit('load');await first
  assert.equal(images[0].opacity,1)
  assert.ok(Math.abs((images[0].bounds[0][0]+images[0].bounds[1][0])/2-23.123457)<1e-10)
  const second=layer.refresh();await tick()
  assert.equal(images[0].removed,false)
  images[1].emit('load');await second
  assert.equal(images[0].removed,true)
  assert.deepEqual(revoked,['blob:test-0'])
  layer.onRemove(map)
  assert.equal(images[1].removed,true)
  assert.equal(map.handlers.size,0)
  assert.deepEqual(revoked,['blob:test-0','blob:test-1'])
  assert.equal(states.at(-1)[0],'ready')
})
test('移动时中止旧请求，卸载后不再回报错误或生成图片',async()=>{
  images.length=0
  const states=[],map=fakeMap()
  let signal
  const layer=amapStaticLayer((p,s)=>{signal=s;return new Promise((resolve,reject)=>{
    s.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true})
  })},(...s)=>states.push(s))
  start(layer,map)
  const request=layer.refresh()
  layer.schedule()
  assert.equal(signal.aborted,true)
  layer.onRemove(map);await request
  assert.equal(images.length,0)
  assert.equal(states.filter(s=>s[0]==='error').length,0)
})
test('迟到的取图响应在卸载后丢弃',async()=>{
  images.length=0
  const map=fakeMap(),states=[]
  let resolve
  const layer=amapStaticLayer(()=>new Promise(r=>{resolve=r}),(...s)=>states.push(s))
  start(layer,map)
  const request=layer.refresh()
  layer.onRemove(map);resolve(new Blob(['late']));await request
  assert.equal(images.length,0)
  assert.deepEqual(states.map(s=>s[0]),['loading'])
})
test('服务错误和图片解码错误可传给界面，临时图片会清理',async(t)=>{
  images.length=0
  const revoked=[]
  t.mock.method(URL,'createObjectURL',()=> 'blob:bad')
  t.mock.method(URL,'revokeObjectURL',u=>revoked.push(u))
  const map=fakeMap(),states=[]
  const failing=amapStaticLayer(async()=>{throw new Error('权限或额度错误')},(...s)=>states.push(s))
  start(failing,map);await failing.refresh();failing.onRemove(map)
  assert.deepEqual(states.at(-1),['error','权限或额度错误'])
  const decode=amapStaticLayer(async()=>new Blob(['bad']),(...s)=>states.push(s))
  start(decode,map)
  const request=decode.refresh();await tick();images[0].emit('error');await request
  assert.equal(states.at(-1)[0],'error')
  assert.equal(images[0].removed,true)
  assert.deepEqual(revoked,['blob:bad'])
  decode.onRemove(map)
})

test('模拟地图渲染浓度图例，不把模拟浓度标作实测污染指数',async()=>{
  globalThis.localStorage={getItem:()=>null,setItem:()=>{},removeItem:()=>{}}
  const {default:TraceMap}=await server.ssrLoadModule('/src/components/TraceMap.vue')
  const app=createSSRApp(TraceMap,{simulation:{label:'氨氮',color_scale:{min:0,max:2,inverse:false},
    mapping:{positions:[[23,113],[23.001,113]],reason:'当前项目轨迹示意'},notice:'恒定流情景',
    concentrations:[[0,2]],times:[0],chainage:[0,100],source:{}}})
  app.use(createPinia())
  const html=await renderToString(app)
  assert.match(html,/模拟氨氮/)
  assert.match(html,/本次模拟全时段统一色标/)
  assert.doesNotMatch(html,/相对.*类的综合污染指数/)
})
test('没有模拟结果时播放器显示操作入口，不呈现伪造扩散',async()=>{
  globalThis.localStorage={getItem:()=>null,setItem:()=>{},removeItem:()=>{}}
  const {default:Player}=await server.ssrLoadModule('/src/components/PollutionPlayback.vue')
  const app=createSSRApp(Player);app.use(createPinia())
  app.component('RouterLink',{props:['to'],setup(props,{slots}){return()=>h('a',{href:props.to},slots.default?.())}})
  const html=await renderToString(app)
  assert.match(html,/当前项目还没有污染扩散模拟结果/)
  assert.match(html,/href="\/river"/)
  assert.doesNotMatch(html,/aria-label="污染扩散时间轴"/)
})

test('恢复旧检测结果时地图提示重新计算，当前结果不显示更新提示',async()=>{
  globalThis.localStorage={getItem:()=>null,setItem:()=>{},removeItem:()=>{}}
  const {default:TraceMap}=await server.ssrLoadModule('/src/components/TraceMap.vue')
  const {useSessionStore}=await server.ssrLoadModule('/src/stores/session.js')
  for(const outdated of [true,false]) {
    const pinia=createPinia(),store=useSessionStore(pinia)
    store.status.detection_outdated=outdated
    const app=createSSRApp(TraceMap);app.use(pinia)
    const html=await renderToString(app)
    if(outdated) assert.match(html,/更新检测/)
    else assert.doesNotMatch(html,/更新检测/)
  }
})

test('检测面板将非黑臭指数线索和观测跨度明确显示',async()=>{
  const {default:Result}=await server.ssrLoadModule('/src/components/BlackSpotResult.vue')
  const html=await renderToString(createSSRApp(Result,{data:{result:{
    '非黑臭指数异常点数':4,'聚类详情':[{cluster_id:0,count:6,'首末观测跨度_min':60}],
  }}}))
  assert.match(html,/指数线索（非黑臭）/)
  assert.match(html,/首末观测跨度 60 min/)
  assert.doesNotMatch(html,/时空额外发现|持续 60 min/)
})
