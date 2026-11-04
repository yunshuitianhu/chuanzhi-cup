import test from 'node:test'
import assert from 'node:assert/strict'
import { mountRubberNavigation } from '../src/utils/rubberNavigation.js'

function setup(t, reduced = false) {
  let now = 0, nextFrame = 0, scale = 1, callback
  const frames = new Map(), listeners = new Map(), mediaListeners = new Map()
  const motion = { matches: reduced,
    addEventListener: (key, fn) => mediaListeners.set(key, fn),
    removeEventListener: key => mediaListeners.delete(key) }
  const copies = Array.from({ length: 4 }, () => ({ style: {} }))
  const values = ['/chat', '/charts', '/river', '/tech']
  const links = values.map((value, i) => ({ dataset: { rubberValue: value },
    getBoundingClientRect: () => ({ left: 104 + i * 110 * scale, right: 104 + (i * 110 + 106) * scale }) }))
  const classes = new Set()
  const track = { dataset: {}, scrollLeft: 0, clientWidth: 448,
    classList: { add: value => classes.add(value), remove: value => classes.delete(value) },
    getBoundingClientRect: () => ({ left: 100 }), querySelectorAll: () => links }
  const thumb = { style: {}, querySelectorAll: () => copies }
  let disconnected = false
  const globals = {
    window: { matchMedia: () => motion },
    document: { hidden: false, addEventListener: (key, fn) => listeners.set(key, fn),
      removeEventListener: key => listeners.delete(key) },
    ResizeObserver: class {
      constructor(fn) { callback = fn }
      observe() {}
      disconnect() { disconnected = true }
    },
    requestAnimationFrame: fn => { frames.set(++nextFrame, fn); return nextFrame },
    cancelAnimationFrame: id => frames.delete(id),
  }
  const restore = []
  for (const [key, value] of Object.entries(globals)) {
    const old = Object.getOwnPropertyDescriptor(globalThis, key)
    Object.defineProperty(globalThis, key, { value, writable: true, configurable: true })
    restore.push(() => old ? Object.defineProperty(globalThis, key, old) : delete globalThis[key])
  }
  const controller = mountRubberNavigation(track, thumb, '/chat')
  t.after(() => { controller.destroy(); restore.forEach(fn => fn()) })
  return { controller, track, thumb, copies, classes, frames, listeners, mediaListeners, motion,
    get disconnected() { return disconnected },
    resize() { scale = 1.2; callback() },
    step(count = 1) {
      for (let i = 0; i < count; i++) {
        const pending = [...frames.values()]; frames.clear(); now += 16
        pending.forEach(fn => fn(now))
      }
    },
    edges() {
      const [, right, left] = thumb.style.clipPath.match(/inset\(0 ([\d.e+-]+)px 0 ([\d.e+-]+)px/)
      return [Number(left), parseFloat(thumb.style.width) - Number(right)]
    },
  }
}

test('成功选中后滑块先跨间隙拉伸，再回弹到对应页面入口', t => {
  const c = setup(t)
  assert.deepEqual(c.edges(), [0, 106])
  c.controller.select('/river'); c.step(5)
  const [left, right] = c.edges()
  assert.ok(right - left > 106)
  c.step(120)
  assert.equal(c.track.dataset.rubberStage, 'idle')
  assert.deepEqual(c.edges(), [220, 326])
  assert.equal(c.copies[2].style.left, '220px')
})

test('快速连续切换只落在最新路径，不回跳到过期动画目标', t => {
  const c = setup(t)
  c.controller.select('/tech'); c.step(5)
  c.controller.select('/charts'); c.step(2)
  c.controller.select('/chat'); c.step(120)
  assert.deepEqual(c.edges(), [0, 106])
  assert.equal(c.frames.size, 0)
})

test('尺寸变化重新测量当前目标；没有匹配路径时恢复链接自身选中样式', t => {
  const c = setup(t)
  c.controller.select('/river'); c.step(3); c.resize()
  const [left, right] = c.edges()
  assert.ok(Math.abs(left - 264) < .001)
  assert.ok(Math.abs(right - 391.2) < .001)
  assert.equal(c.frames.size, 0)
  c.controller.select('/unknown')
  assert.equal(c.thumb.style.opacity, '0')
  assert.equal(c.classes.has('rubber-ready'), false)
})

test('减少动态效果直接定位，偏好变化也会停止正在执行的动画', t => {
  const c = setup(t, true)
  c.controller.select('/tech')
  assert.deepEqual(c.edges(), [330, 436])
  assert.equal(c.frames.size, 0)
  c.motion.matches = false; c.controller.select('/charts'); c.step(2)
  c.motion.matches = true; c.mediaListeners.get('change')()
  assert.deepEqual(c.edges(), [110, 216])
  assert.equal(c.frames.size, 0)
})

test('卸载取消动画、观察器和监听；迟到测量不再操作节点', t => {
  const c = setup(t)
  c.controller.select('/tech'); c.step(2); c.controller.destroy()
  const before = c.thumb.style.clipPath
  c.resize(); c.controller.select('/river'); c.step()
  assert.equal(c.thumb.style.clipPath, before)
  assert.equal(c.thumb.style.opacity, '0')
  assert.equal(c.frames.size, 0)
  assert.equal(c.listeners.size, 0)
  assert.equal(c.mediaListeners.size, 0)
  assert.equal(c.disconnected, true)
})
