import test from 'node:test'
import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'
import { createSSRApp, h } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { createPinia } from 'pinia'

const server=await createServer({
  configLoader:'runner',
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

test('技术页直接显示恢复的检测结果，包含零预警情形',async()=>{
  globalThis.localStorage={getItem:()=>null,setItem:()=>{},removeItem:()=>{}}
  const {default:Tech}=await server.ssrLoadModule('/src/views/TechView.vue')
  const {useSessionStore}=await server.ssrLoadModule('/src/stores/session.js')
  const pinia=createPinia(),store=useSessionStore(pinia)
  store.blackSpots={result:{'黑点总数':0,'提示':'恢复的零预警结果'}}
  store.status.has_detection=true;store.status.detection_outdated=true
  const app=createSSRApp(Tech);app.use(pinia)
  const html=await renderToString(app)
  assert.match(html,/恢复的零预警结果/)
  assert.match(html,/检测结果使用旧算法/)
})

test('交叉印证恢复旧项目比例时改用参照表述，保留零值',async()=>{
  globalThis.localStorage={getItem:()=>null,setItem:()=>{},removeItem:()=>{}}
  const {default:Cross}=await server.ssrLoadModule('/src/components/CrossValidation.vue')
  const {useSessionStore}=await server.ssrLoadModule('/src/stores/session.js')
  const pinia=createPinia(),store=useSessionStore(pinia)
  store.status.video_frames=3
  store.crossValidation={'时间对齐':{'时间偏移秒':-5,'匹配窗口秒':20,'匹配成功帧数':3,'总视频帧数':3},
    '黑臭判定一致性':{'一致率_%':0,'视频判黑准确率_%':0,'视频判黑召回率_%':0}}
  const app=createSSRApp(Cross);app.use(pinia)
  const html=await renderToString(app)
  assert.match(html,/视频预警符合率/);assert.match(html,/传感器预警的视频覆盖率/)
  assert.match(html,/未经过人工真值复核/)
  assert.doesNotMatch(html,/>视频判黑准确率</)
  assert.match(html,/value="-5"/);assert.match(html,/value="20"/)
  assert.ok((html.match(/>0<small/g)||[]).length>=3)
})

test('已有监测表时可单独加载视频，成功后清除文件选择',async()=>{
  globalThis.localStorage={getItem:()=>null,setItem:()=>{},removeItem:()=>{}}
  const {default:DataPanel}=await server.ssrLoadModule('/src/components/DataPanel.vue')
  const {useSessionStore}=await server.ssrLoadModule('/src/stores/session.js')
  const pinia=createPinia(),store=useSessionStore(pinia)
  store.status.has_data=true
  const calls=[];store.uploadVideo=async file=>{calls.push(file.name);return {frames:3,report:'完成'}}
  store.uploadData=()=>{throw new Error('不应重载监测表')}
  let controls
  const app=createSSRApp(DataPanel);app.use(pinia)
  app.mixin({created(){if(this.$options.__file?.endsWith('DataPanel.vue'))controls=this.$.setupState}})
  await renderToString(app)
  assert.ok(controls?.onPick,'实际组件处理函数可访问')
  controls.onPick('video',{target:{files:[{name:'new.mp4'}]}})
  assert.equal(controls.canLoad,true);assert.equal(controls.loadLabel,'加载视频')
  await controls.load()
  assert.deepEqual(calls,['new.mp4'])
  assert.equal(controls.videoFile,null)
  assert.equal(controls.canLoad,false)
})

test('统计与时间序列的迟到响应不能覆盖较新数据',async()=>{
  const {useSessionStore}=await server.ssrLoadModule('/src/stores/session.js')
  for(const [file,method,value] of [
    ['/src/components/SummaryCards.vue','load','data'],
    ['/src/views/ChartsView.vue','loadTimeSeries','ts'],
    ['/src/components/DataQualityPanel.vue','load','result'],
  ]) {
    const {default:Component}=await server.ssrLoadModule(file)
    const pinia=createPinia(),store=useSessionStore(pinia);store.status.has_data=true
    let controls
    const app=createSSRApp(Component);app.use(pinia)
    app.component('RouterLink',{props:['to'],setup(props,{slots}){return()=>h('a',{href:props.to},slots.default?.())}})
    app.mixin({created(){if(this.$options.__file?.endsWith(file.split('/').at(-1)))controls=this.$.setupState}})
    await renderToString(app)
    let resolveOld,resolveNew,count=0
    globalThis.fetch=async()=>new Promise(resolve=>{if(count++===0)resolveOld=resolve;else resolveNew=resolve})
    const old=controls[method](),fresh=controls[method]()
    resolveNew(new Response('{"marker":"new"}',{status:200}));await fresh
    resolveOld(new Response('{"marker":"old"}',{status:200}));await old
    assert.equal(controls[value].marker,'new',file)
  }
})

test('质量摘要同时保留原始预警和复核数，不显示确认故障',async()=>{
  const {default:Quality}=await server.ssrLoadModule('/src/components/QualitySummary.vue')
  const html=await renderToString(createSSRApp(Quality,{summary:{
    '原始阈值预警点数':24,'需质控复核预警点数':24,'需复核记录数':24,'关键指标缺测记录数':36,
    '原始值已保留':{ammonia_nitrogen:false,dissolved_oxygen:false},
  }}))
  assert.match(html,/原始阈值预警/)
  assert.match(html,/预警中需质控复核/)
  assert.match(html,/不撤销阈值预警/)
  assert.match(html,/旧处理值不能当原值/)
  assert.doesNotMatch(html,/已确认故障|清洗后无污染|合格率/)
})

test('复核清单显示原始记录并转义源表文本',async()=>{
  const {default:Panel}=await server.ssrLoadModule('/src/components/DataQualityPanel.vue')
  const {useSessionStore}=await server.ssrLoadModule('/src/stores/session.js')
  const pinia=createPinia(),store=useSessionStore(pinia);store.status.has_data=true
  const app=createSSRApp(Panel);app.use(pinia)
  app.mixin({created(){if(this.$options.__file?.endsWith('DataQualityPanel.vue')) this.$.setupState.result={
    summary:null,total:1,records:[{id:2,time:'2026-10-19',label:'输入无效',input_record_id:3,
      raw_text:{ammonia_nitrogen:'<img src=x onerror=alert(1)>'},raw_preserved:{ammonia_nitrogen:true},reasons:[]}],
  }}})
  const html=await renderToString(app)
  assert.match(html,/采样点 #3/)
  assert.match(html,/排序前导入记录 #3/)
  assert.match(html,/&lt;img/)
  assert.doesNotMatch(html,/<img src=x/)
})

test('质控设置可提交响应式表单且项目更新后丢弃旧草稿',async()=>{
  const {default:Panel}=await server.ssrLoadModule('/src/components/DataQualityPanel.vue')
  const {useSessionStore}=await server.ssrLoadModule('/src/stores/session.js')
  const pinia=createPinia(),store=useSessionStore(pinia);store.status.has_data=true
  const config={max_gap_s:60,flat_duration_s:120,flat_min_points:5,
    spike_thresholds:{ammonia_nitrogen:2,dissolved_oxygen:.5},
    flat_tolerances:{ammonia_nitrogen:.001,dissolved_oxygen:.001}}
  let controls,submitted
  store.updateQualityConfig=async value=>{submitted=value}
  const app=createSSRApp(Panel);app.use(pinia)
  app.mixin({created(){if(this.$options.__file?.endsWith('DataQualityPanel.vue'))controls=this.$.setupState}})
  await renderToString(app)
  globalThis.fetch=async()=>new Response(JSON.stringify({summary:{'参数':config},records:[],total:0}),{status:200})
  await controls.load()
  controls.settings.spike_thresholds.ammonia_nitrogen=3
  await controls.saveSettings()
  assert.equal(submitted.spike_thresholds.ammonia_nitrogen,3)
  assert.equal(controls.error,'');assert.equal(controls.saved,true)
  controls.resetData()
  assert.equal(controls.settings,null);assert.equal(controls.saved,false)
  // 等待本次加载，以免迟到的测试请求混入下一用例。
  await controls.load()
  assert.equal(controls.settings.spike_thresholds.ammonia_nitrogen,2)
})
