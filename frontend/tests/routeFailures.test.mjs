// 只测试纯工具函数与注入的 location/reload/navigate；不启动浏览器，不加载 Vue/Pinia。
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  APP_ROUTES,
  RELOAD_WARNING,
  classifyRouteError,
  createRouteLoadRecovery,
  parseAppLocation,
  safeAppPath,
} from '../src/utils/routeFailures.js'

const chunkError = () => new TypeError('Failed to fetch dynamically imported module: http://127.0.0.1:5190/assets/RiverView-OLD.js')

function setup({ location = { pathname: '/chat', search: '', hash: '' }, navigate, reload } = {}) {
  const notices = []
  const calls = []
  let busy = false
  const recovery = createRouteLoadRecovery({
    location,
    reload: reload || (() => calls.push('reload')),
    navigate: navigate || ((href) => calls.push(href)),
    isBusy: () => busy,
    notify: (next) => notices.push(next),
  })
  return { recovery, notices, calls, latest: () => notices.at(-1), setBusy: (value) => { busy = value } }
}

test('区分懒加载模块、CSS 分块失败与普通运行时错误', () => {
  assert.equal(classifyRouteError(chunkError()).kind, 'chunk')
  assert.equal(classifyRouteError(new TypeError('error loading dynamically imported module: /assets/TechView-OLD.js')).kind, 'chunk')
  assert.equal(classifyRouteError(Object.assign(new Error('加载失败'), { name: 'ChunkLoadError' })).kind, 'chunk')
  const css = Object.assign(new Error('Unable to preload CSS for /assets/RiverView-OLD.css'), { code: 'CSS_CHUNK_LOAD_FAILED' })
  assert.equal(classifyRouteError(css).kind, 'css')

  const runtimes = [new TypeError('Failed to fetch'), new Error('Cannot read properties of null'), undefined, null, '', {}]
  for (const plain of runtimes) {
    const info = classifyRouteError(plain)
    assert.equal(info.kind, 'runtime')
    assert.equal(info.retryable, false)
  }
})

test('目标地址只允许四个应用路由，并净化查询/哈希', () => {
  assert.deepEqual([...APP_ROUTES], ['/chat', '/charts', '/river', '/tech'])
  assert.equal(safeAppPath('/river'), '/river')
  assert.equal(safeAppPath('/river/'), '/river')
  assert.equal(safeAppPath('/'), '/chat')
  assert.equal(safeAppPath('/river?tab=2#sec-3'), '/river?tab=2#sec-3')
  assert.deepEqual(parseAppLocation('/tech?a=1#b'), { path: '/tech', query: '?a=1', hash: '#b', href: '/tech?a=1#b' })

  assert.equal(safeAppPath('//evil.test/river'), null)
  assert.equal(safeAppPath('https://evil.test/river'), null)
  assert.equal(safeAppPath('/admin'), null)
  assert.equal(safeAppPath('/river%2e%2e/chat'), null)
  assert.equal(safeAppPath('/river\\..\\chat'), null)
  assert.equal(safeAppPath(null), null)

  assert.equal(safeAppPath('/river?x=<script>'), '/river')
  assert.equal(safeAppPath('/river#"><img src=x>'), '/river')
  assert.equal(safeAppPath(`/chat?q=${'a'.repeat(400)}`), null)
})

test('懒加载失败：保留当前页、不自动跳转，提示为固定文案', () => {
  const { recovery, calls, latest } = setup()
  const result = recovery.onError(chunkError(), { path: '/river', fullPath: '/river', meta: { title: '河道模型' } })
  assert.deepEqual(result, { handled: true, kind: 'chunk', retryable: true, href: '/river' })
  assert.deepEqual(calls, [])

  const notice = latest()
  assert.equal(notice.href, '/river')
  assert.equal(notice.title, '河道模型')
  assert.equal(notice.message, '页面资源加载失败，可能是版本更新或网络中断；当前页仍可继续使用。')
  assert.equal(notice.warning, RELOAD_WARNING)
  assert.equal(notice.canReload, true)
  assert.equal(notice.busy, false)
  assert.equal(notice.blockedReason, null)
  assert.ok(Object.isFrozen(notice))

  const text = JSON.stringify(notice)
  assert.ok(!text.includes('http'))
  assert.ok(!text.includes('<'))
  assert.ok(!text.includes('OLD'))
})

test('标题来源去掉标记字符并限长', () => {
  const { recovery, latest } = setup()
  recovery.onError(chunkError(), { path: '/river', meta: { title: '<img src=x onerror=alert(1)>河道' } })
  const { title } = latest()
  assert.ok(!/[<>]/.test(title))
  assert.ok(title.length <= 24)
})

test('普通路由错误也显示固定边界提示，保留当前页且不泄露错误原文', () => {
  const { recovery, calls, latest } = setup()
  const result = recovery.onError(new TypeError('PRIVATE_ERROR_DETAIL'), { path: '/river' })
  assert.equal(result.kind, 'runtime')
  assert.match(latest().message, /暂时无法打开/)
  assert.ok(!JSON.stringify(latest()).includes('PRIVATE_ERROR_DETAIL'))
  assert.deepEqual(calls, [])
})

test('任务进行中禁止刷新并说明原因', () => {
  const { recovery, calls, latest, setBusy } = setup()
  setBusy(true)
  recovery.onError(chunkError(), { path: '/river' })
  assert.equal(latest().canReload, false)
  assert.equal(latest().busy, true)
  assert.match(latest().blockedReason, /分析|对话/)

  const blocked = recovery.retry()
  assert.equal(blocked.ok, false)
  assert.equal(blocked.reason, 'busy')
  assert.deepEqual(calls, [])

  setBusy(false)
  const ok = recovery.retry()
  assert.equal(ok.ok, true)
  assert.deepEqual(calls, ['/river'])
})

test('刷新只触发一次，重复点击不会形成刷新循环', () => {
  const { recovery, calls, latest } = setup()
  recovery.onError(chunkError(), { path: '/river' })
  assert.equal(recovery.retry().ok, true)
  const second = recovery.retry()
  assert.equal(second.ok, false)
  assert.equal(second.reason, 'reload-pending')
  assert.deepEqual(calls, ['/river'])
  assert.match(latest().reloadLabel, /重新加载/)
  assert.equal(recovery.isReloadPending(), true)
})

test('刷新发出后再次报错不会二次导航，仍显示原目标', () => {
  const { recovery, calls, latest } = setup()
  recovery.onError(chunkError(), { path: '/river' })
  recovery.retry()
  const again = recovery.onError(chunkError(), { path: '/tech' })
  assert.equal(again.href, '/river')
  assert.equal(recovery.getNotice().href, '/river')
  assert.match(latest().blockedReason, /已请求刷新/)
  assert.equal(recovery.retry().reason, 'reload-pending')
  assert.deepEqual(calls, ['/river'])
})

test('默认导航：同路由整页刷新，跨路由 assign 到白名单地址', () => {
  const calls = []
  const base = { pathname: '/river', search: '', hash: '', assign: (url) => calls.push(`assign:${url}`) }

  const same = createRouteLoadRecovery({ location: base, reload: () => calls.push('reload'), notify: () => {} })
  same.onError(chunkError(), { path: '/river' })
  same.retry()
  assert.deepEqual(calls, ['reload'])

  const other = createRouteLoadRecovery({
    location: { ...base, pathname: '/chat' },
    reload: () => calls.push('reload'),
    notify: () => {},
  })
  other.onError(chunkError(), { path: '/river' })
  other.retry()
  assert.equal(calls.at(-1), 'assign:/river')
})

test('未列入白名单的目标回退到当前允许页面', () => {
  const { recovery, calls } = setup()
  const result = recovery.onError(chunkError(), { path: '/admin', fullPath: '/admin?x=1' })
  assert.equal(result.handled, true)
  assert.equal(result.href, '/chat')
  assert.equal(recovery.getNotice().href, '/chat')
  recovery.retry()
  assert.deepEqual(calls, ['/chat'])

  recovery.dismiss()
  recovery.onError(chunkError(), '/tech')
  assert.equal(recovery.getNotice().href, '/tech')
})

test('保留安全的查询与哈希，危险字符被丢弃', () => {
  const { recovery, calls } = setup()
  recovery.onError(chunkError(), { path: '/river', fullPath: '/river?tab=2#sec-3' })
  assert.equal(recovery.getNotice().href, '/river?tab=2#sec-3')
  recovery.retry()
  assert.deepEqual(calls, ['/river?tab=2#sec-3'])

  const second = setup()
  second.recovery.onError(chunkError(), { path: '/river', fullPath: '/river?x=<img>#"><b>' })
  assert.equal(second.recovery.getNotice().href, '/river')
})

test('导航被拦截时保持提示并允许再次尝试', () => {
  let fail = true
  const calls = []
  const { recovery, latest } = setup({
    navigate: (href) => {
      if (fail) throw new Error('blocked by browser')
      calls.push(href)
    },
  })
  recovery.onError(chunkError(), { path: '/charts' })
  const first = recovery.retry()
  assert.equal(first.ok, false)
  assert.equal(first.reason, 'navigate-failed')
  assert.deepEqual(calls, [])
  assert.equal(recovery.isReloadPending(), false)
  assert.equal(latest().canReload, true)
  assert.match(latest().blockedReason, /手动刷新/)

  fail = false
  assert.equal(recovery.retry().ok, true)
  assert.deepEqual(calls, ['/charts'])
})

test('仅成功导航清除提示；失败导航与未匹配导航都保留提示', () => {
  const { recovery } = setup()
  recovery.onError(chunkError(), { path: '/river' })

  assert.equal(recovery.afterEach({ path: '/chat', matched: [{}] }, {}, new Error('aborted')), false)
  assert.notEqual(recovery.getNotice(), null)

  assert.equal(recovery.afterEach({ path: '/nope', matched: [] }, {}, undefined), false)
  assert.notEqual(recovery.getNotice(), null)

  assert.equal(recovery.afterEach({ path: '/river', matched: [{}] }, {}, undefined), true)
  assert.equal(recovery.getNotice(), null)
  assert.equal(recovery.isReloadPending(), false)
})

test('手动关闭提示后不再触发刷新', () => {
  const { recovery, calls } = setup()
  recovery.onError(chunkError(), { path: '/river' })
  assert.equal(recovery.dismiss(), true)
  assert.equal(recovery.getNotice(), null)
  assert.equal(recovery.retry().reason, 'no-notice')
  assert.deepEqual(calls, [])
  assert.equal(recovery.dismiss(), false)
})

test('attach 注册 onError/afterEach 并返回解绑函数', () => {
  const handlers = { error: [], after: [] }
  const router = {
    onError: (fn) => {
      handlers.error.push(fn)
      return () => { handlers.error = handlers.error.filter((item) => item !== fn) }
    },
    afterEach: (fn) => {
      handlers.after.push(fn)
      return () => { handlers.after = handlers.after.filter((item) => item !== fn) }
    },
  }
  const { recovery } = setup()
  const detach = recovery.attach(router)

  for (const fn of handlers.error) fn(chunkError(), { path: '/tech', meta: { title: '技术展示' } })
  assert.equal(recovery.getNotice().href, '/tech')

  for (const fn of handlers.after) fn({ path: '/tech', matched: [{}] }, {}, undefined)
  assert.equal(recovery.getNotice(), null)

  detach()
  assert.equal(handlers.error.length, 0)
  assert.equal(handlers.after.length, 0)
  assert.throws(() => recovery.attach(null), TypeError)
})

test('忙碌结束后恢复按钮可用，不必再次触发错误', () => {
  const { recovery, latest, setBusy } = setup()
  setBusy(true); recovery.onError(chunkError(), '/tech')
  setBusy(false); recovery.refreshBusy()
  assert.equal(latest().canReload,true)
  assert.equal(latest().blockedReason,null)
})
test('相同路由但查询或哈希不同，明确导航到所请求的地址', () => {
  const calls=[]
  const recovery=createRouteLoadRecovery({location:{pathname:'/river',search:'?a=1',hash:'#old',assign:href=>calls.push(href)},reload:()=>calls.push('reload')})
  recovery.onError(chunkError(),'/river?a=2#new'); recovery.retry()
  assert.deepEqual(calls,['/river?a=2#new'])
})

test('真实Vue Router导航守卫：懒加载失败保留当前路由，后续成功导航清除提示',async()=>{
 const {createRouter,createMemoryHistory}=await import('vue-router')
 const router=createRouter({history:createMemoryHistory(),routes:[
  {path:'/chat',component:{}},{path:'/river',component:()=>Promise.reject(chunkError())},
  {path:'/tech',component:{}}
 ]})
 const recovery=createRouteLoadRecovery({location:{pathname:'/chat'}})
 const detach=recovery.attach(router)
 try{
  await router.push('/chat'); await assert.rejects(router.push('/river'))
  assert.equal(router.currentRoute.value.path,'/chat')
  assert.equal(recovery.getNotice().path,'/river')
  await router.push('/tech'); assert.equal(router.currentRoute.value.path,'/tech')
  assert.equal(recovery.getNotice(),null)
 }finally{detach()}
})
