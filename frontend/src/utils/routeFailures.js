/**
 * Vue Router 懒加载失败的最小恢复工具（无框架依赖，便于 Node 直接测试）。
 *
 * 边界：
 * - 只接管“动态 import / CSS 分块加载失败”，这类问题只能靠整页重新加载修复；
 *   普通路由错误显示固定提示，并保留原有诊断信息。
 * - 出错时什么都不做：不 router.push、不 location.reload，当前可用的页面保持原样。
 * - 刷新只能由用户点击触发，同一待恢复状态只发出一次；成功切换页面后可处理新故障。
 * - 目标地址只允许四个应用路由（可附带净化后的查询/哈希），永不跳转外部地址。
 * - 不保存项目、不新建项目、不清理草稿，只提示用户先自行保存/导出/复制草稿。
 */

export const APP_ROUTES = Object.freeze(['/chat', '/charts', '/river', '/tech'])

export const RELOAD_WARNING = '刷新会整页重新加载：请先保存或导出项目，并复制尚未发送的草稿；未保存的内容会丢失。'
export const BUSY_REASON = '分析、对话或项目切换正在进行，请完成后再刷新。'
export const PENDING_TEXT = '已请求刷新；若页面仍未更新，请先保存内容后手动刷新浏览器。'
export const MANUAL_REASON = '刷新未能启动，请手动刷新浏览器（Ctrl+R / Cmd+R）。'

const FALLBACK_PATH = '/chat'
const MAX_INPUT = 300
const MAX_QUERY = 160
const MAX_HASH = 64
const SAFE_QUERY = /^\?[A-Za-z0-9\-._~=&+]*$/
const SAFE_HASH = /^#[A-Za-z0-9\-._~]*$/
const TITLE_STRIP = /[<>&"'`\\]/g
const PENDING_LABEL = '正在重新加载…'
const RETRY_LABEL = '刷新以加载最新版本'

const CSS_PATTERNS = [
  /unable to preload css/i,
  /failed to load css/i,
  /css chunk/i,
]

const CHUNK_PATTERNS = [
  /failed to fetch dynamically imported module/i,
  /error loading dynamically imported module/i,
  /importing a module script failed/i,
  /dynamically imported module/i,
]

const CSS_CODES = new Set(['CSS_CHUNK_LOAD_FAILED'])
const CHUNK_CODES = new Set(['ChunkLoadError', 'DYNAMIC_IMPORT_FAILED', 'MODULE_LOAD_FAILED'])

const MESSAGES = Object.freeze({
  chunk: '页面资源加载失败，可能是版本更新或网络中断；当前页仍可继续使用。',
  css: '页面样式资源加载失败，当前页仍可继续使用。',
  runtime: '目标页面暂时无法打开，当前页仍可继续使用。可先切换其他页面，保存内容后重试。',
})

/** 只做分类，不产生任何副作用。 */
export function classifyRouteError(error) {
  const message = typeof error === 'string'
    ? error
    : (error && typeof error.message === 'string' ? error.message : '')
  const name = error && typeof error === 'object' && typeof error.name === 'string' ? error.name : ''
  const code = error && typeof error === 'object' && typeof error.code === 'string' ? error.code : ''

  if (CSS_CODES.has(code) || CSS_CODES.has(name) || CSS_PATTERNS.some((pattern) => pattern.test(message))) {
    return { kind: 'css', retryable: true }
  }
  if (CHUNK_CODES.has(code) || CHUNK_CODES.has(name) || CHUNK_PATTERNS.some((pattern) => pattern.test(message))) {
    return { kind: 'chunk', retryable: true }
  }
  return { kind: 'runtime', retryable: false }
}

/**
 * 解析并净化一个应用内地址。返回 null 表示不属于白名单路由。
 * 查询/哈希只在字符集与长度都安全时保留，否则直接丢弃。
 */
export function parseAppLocation(raw, allowed = APP_ROUTES) {
  if (typeof raw !== 'string') return null
  const value = raw.trim()
  if (!value || value.length > MAX_INPUT) return null
  if (!value.startsWith('/') || value.startsWith('//') || value.includes('\\')) return null

  const hashAt = value.indexOf('#')
  const beforeHash = hashAt === -1 ? value : value.slice(0, hashAt)
  let hash = hashAt === -1 ? '' : value.slice(hashAt)
  const queryAt = beforeHash.indexOf('?')
  let query = queryAt === -1 ? '' : beforeHash.slice(queryAt)
  let pathname = queryAt === -1 ? beforeHash : beforeHash.slice(0, queryAt)

  pathname = pathname.replace(/\/+$/, '') || '/'
  if (pathname === '/') pathname = FALLBACK_PATH
  if (!allowed.includes(pathname)) return null

  if (query && (query.length > MAX_QUERY || !SAFE_QUERY.test(query))) query = ''
  if (hash && (hash.length > MAX_HASH || !SAFE_HASH.test(hash))) hash = ''
  return { path: pathname, query, hash, href: `${pathname}${query}${hash}` }
}

export function safeAppPath(raw, allowed = APP_ROUTES) {
  const parsed = parseAppLocation(raw, allowed)
  return parsed ? parsed.href : null
}

function attemptedLocation(to, allowed) {
  if (!to) return null
  if (typeof to === 'string') return parseAppLocation(to, allowed)
  const full = typeof to.fullPath === 'string' ? to.fullPath : null
  const parsedFull = full ? parseAppLocation(full, allowed) : null
  if (parsedFull) return parsedFull
  return typeof to.path === 'string' ? parseAppLocation(to.path, allowed) : null
}

function attemptedTitle(to) {
  const raw = to && typeof to === 'object' && to.meta && typeof to.meta.title === 'string' ? to.meta.title : ''
  const clean = raw.replace(TITLE_STRIP, '').replace(/\s+/g, ' ').trim().slice(0, 24)
  return clean || null
}

/**
 * @param {object} [options]
 * @param {object|Function} [options.location] 注入的 location（对象或返回对象的函数），默认 globalThis.location。
 * @param {Function} [options.reload] 注入的“整页刷新”，默认 location.reload()。
 * @param {Function} [options.navigate] 注入的“跳到地址”，默认同路由 reload、跨路由 assign。
 * @param {Function} [options.isBusy] 返回 true 时禁止刷新（分析/对话/项目切换中）。
 * @param {Function} [options.notify] 状态回调，收到冻结的提示对象或 null。
 * @param {string[]} [options.allowedRoutes] 允许的目标路由，默认 APP_ROUTES。
 */
export function createRouteLoadRecovery(options = {}) {
  const allowed = Array.isArray(options.allowedRoutes) && options.allowedRoutes.length
    ? options.allowedRoutes.filter((path) => typeof path === 'string')
    : [...APP_ROUTES]
  const notify = typeof options.notify === 'function' ? options.notify : () => {}
  const isBusy = typeof options.isBusy === 'function' ? options.isBusy : () => false
  const readNavigate = typeof options.navigate === 'function' ? options.navigate : null

  let notice = null
  let reloadIssued = false

  function getLocation() {
    const injected = typeof options.location === 'function' ? options.location() : options.location
    const loc = injected || (typeof globalThis === 'object' ? globalThis.location : null)
    return loc && typeof loc === 'object' ? loc : null
  }

  function reloadCurrent() {
    if (typeof options.reload === 'function') {
      options.reload()
      return
    }
    const loc = getLocation()
    if (loc && typeof loc.reload === 'function') loc.reload()
  }

  function navigateTo(href) {
    if (readNavigate) {
      readNavigate(href)
      return
    }
    const loc = getLocation()
    const target = parseAppLocation(href, allowed)
    if (!target) return
    const current = loc && typeof loc.pathname === 'string' ? parseAppLocation(loc.pathname + (loc.search || '') + (loc.hash || ''), allowed) : null
    if (current && current.href === target.href) {
      reloadCurrent()
      return
    }
    if (loc && typeof loc.assign === 'function') {
      loc.assign(target.href)
      return
    }
    if (loc && typeof loc.replace === 'function') {
      loc.replace(target.href)
      return
    }
    reloadCurrent()
  }

  function currentPage() {
    const loc = getLocation()
    const parsed = loc && typeof loc.pathname === 'string' ? parseAppLocation(loc.pathname + (loc.search || '') + (loc.hash || ''), allowed) : null
    return parsed || { path: FALLBACK_PATH, query: '', hash: '', href: FALLBACK_PATH }
  }

  function retryTarget() {
    const parsed = notice ? parseAppLocation(notice.href, allowed) : null
    return parsed || currentPage()
  }

  function emit() {
    notify(notice)
  }

  function publish(next) {
    notice = Object.freeze({ ...next })
    emit()
  }

  function onError(error, to) {
    const info = classifyRouteError(error)

    if (reloadIssued && notice) {
      emit()
      return { handled: true, kind: info.kind, retryable: true, href: notice.href }
    }

    const attempted = attemptedLocation(to, allowed) || currentPage()
    const busy = isBusy() === true
    publish({
      kind: info.kind,
      title: attemptedTitle(to),
      href: attempted.href,
      path: attempted.path,
      message: MESSAGES[info.kind] || MESSAGES.chunk,
      warning: RELOAD_WARNING,
      reloadLabel: RETRY_LABEL,
      canReload: !busy,
      busy,
      blockedReason: busy ? BUSY_REASON : null,
    })
    return { handled: true, kind: info.kind, retryable: true, href: attempted.href }
  }

  /** 只能由用户点击“刷新”调用；永远不会自动触发。 */
  function retry() {
    if (!notice) return { ok: false, reason: 'no-notice' }
    if (reloadIssued) return { ok: false, reason: 'reload-pending' }
    if (isBusy() === true) {
      publish({ ...notice, canReload: false, busy: true, blockedReason: BUSY_REASON, reloadLabel: RETRY_LABEL })
      return { ok: false, reason: 'busy' }
    }

    const target = retryTarget()
    reloadIssued = true
    publish({
      ...notice,
      href: target.href,
      path: target.path,
      canReload: false,
      busy: false,
      blockedReason: PENDING_TEXT,
      reloadLabel: PENDING_LABEL,
    })

    try {
      navigateTo(target.href)
    } catch {
      reloadIssued = false
      publish({
        ...notice,
        href: target.href,
        path: target.path,
        canReload: true,
        busy: false,
        blockedReason: MANUAL_REASON,
        reloadLabel: RETRY_LABEL,
      })
      return { ok: false, reason: 'navigate-failed' }
    }
    return { ok: true, href: target.href, path: target.path }
  }

  function afterEach(to, from, failure) {
    if (failure) return false
    if (!to || !Array.isArray(to.matched) || to.matched.length === 0) return false
    const hadNotice = notice !== null
    if (!hadNotice && !reloadIssued) return false
    notice = null
    reloadIssued = false
    if (hadNotice) emit()
    return hadNotice
  }

  function dismiss() {
    if (!notice) return false
    notice = null
    emit()
    return true
  }

  function refreshBusy() {
    if (!notice || reloadIssued) return
    const busy = isBusy() === true
    if (busy !== notice.busy) publish({ ...notice, busy, canReload: !busy, blockedReason: busy ? BUSY_REASON : null })
  }

  function getNotice() {
    return notice
  }

  function isReloadPending() {
    return reloadIssued
  }

  /** 注册到真实 router；返回解绑函数，便于组件卸载时清理。 */
  function attach(router) {
    if (!router || typeof router.onError !== 'function' || typeof router.afterEach !== 'function') {
      throw new TypeError('attach 需要 Vue Router 实例')
    }
    const offError = router.onError((error, to, from) => { onError(error, to, from) })
    const offAfter = router.afterEach((to, from, failure) => { afterEach(to, from, failure) })
    return () => {
      if (typeof offError === 'function') offError()
      if (typeof offAfter === 'function') offAfter()
    }
  }

  return { onError, afterEach, retry, dismiss, getNotice, isReloadPending, refreshBusy, attach }
}
