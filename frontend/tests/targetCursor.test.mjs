import test from 'node:test'
import assert from 'node:assert/strict'
import { mountTargetCursor } from '../src/utils/targetCursor.js'

function setup(t) {
  const bus = () => ({
    events: new Map(),
    addEventListener(name, fn) { this.events.set(name, fn) },
    removeEventListener(name) { this.events.delete(name) },
    emit(name, event = {}) { this.events.get(name)?.(event) },
  })
  const win = bus(), html = bus(), doc = bus(), frames = new Map()
  let id = 0, now = 0, hit = null
  const styles = []
  Object.assign(win, { matchMedia: () => ({ matches: false }) })
  Object.assign(doc, {
    documentElement: html, hidden: false,
    head: { appendChild: el => styles.push(el) },
    createElement: () => ({ textContent: '', removed: false, remove() { this.removed = true } }),
    elementFromPoint: () => hit,
  })
  const globals = {
    window: win, document: doc, getComputedStyle: () => ({ borderTopLeftRadius: '8px' }),
    requestAnimationFrame: fn => { frames.set(++id, fn); return id },
    cancelAnimationFrame: key => frames.delete(key),
  }
  const restoreGlobals = []
  for (const [name, value] of Object.entries(globals)) {
    const old = Object.getOwnPropertyDescriptor(globalThis, name)
    Object.defineProperty(globalThis, name, { value, configurable: true, writable: true })
    restoreGlobals.push(() => old ? Object.defineProperty(globalThis, name, old) : delete globalThis[name])
  }
  const root = { style: { setProperty(name, value) { this[name] = value } } }
  const corners = Array.from({ length: 4 }, () => ({ style: {} }))
  const cleanup = mountTargetCursor(root, corners, { style: {} })
  t.after(() => { cleanup(); restoreGlobals.forEach(restore => restore()) })
  const element = ({ interactive = false, native = false } = {}) => ({
    isConnected: true, disabled: false,
    closest(selector) {
      if (selector.startsWith('input')) return native || this.disabled ? this : null
      return interactive ? this : null
    },
    matches() { return this.disabled },
    getBoundingClientRect: () => ({ left: 100, top: 100, width: 120, height: 40 }),
    getAttribute: () => null,
  })
  return {
    win, doc, html, root, corners, styles, frames, cleanup, element,
    move(target) { hit = target; win.emit('pointermove', { pointerType: 'mouse', clientX: 130, clientY: 120, target }) },
    tick(count = 60) {
      for (let i = 0; i < count; i++) {
        const pending = [...frames.values()]; frames.clear(); now += 16
        pending.forEach(fn => fn(now))
      }
    },
  }
}

test('目标框跟随并锁定交互控件，输入与地图原生区域恢复系统光标', t => {
  const c = setup(t)
  c.move(c.element({ interactive: true })); c.tick()
  assert.match(c.styles[0].textContent, /cursor: none/)
  assert.ok(Number(c.root.style.opacity) > .99)
  assert.ok(c.corners.every(el => el.style.transform.includes('translate3d') && !el.style.transform.includes('NaN')))
  c.move(c.element({ native: true })); c.tick()
  assert.equal(c.styles[0].textContent, '')
  assert.ok(Number(c.root.style.opacity) < .01)
})

test('悬停控件动态变为禁用时立即恢复原生光标', t => {
  const c = setup(t), button = c.element({ interactive: true })
  c.move(button); c.tick()
  button.disabled = true; c.tick()
  assert.equal(c.styles[0].textContent, '')
})

test('键盘与窗口失焦不会留下隐藏的系统光标', t => {
  const c = setup(t)
  for (const event of ['keydown', 'blur']) {
    c.move(c.element({ interactive: true })); c.tick()
    c.win.emit(event); c.tick()
    assert.equal(c.styles[0].textContent, '')
    assert.ok(Number(c.root.style.opacity) < .01)
  }
})

test('退出工作台清理动画帧、全局监听和隐藏光标样式', t => {
  const c = setup(t)
  c.move(c.element({ interactive: true })); c.tick(3)
  c.cleanup()
  assert.equal(c.frames.size, 0)
  assert.equal(c.win.events.size, 0)
  assert.equal(c.doc.events.size, 0)
  assert.equal(c.html.events.size, 0)
  assert.equal(c.styles[0].removed, true)
})

test('输入框通过聚焦或点击激活时，即使未收到移动事件也恢复原生光标', t => {
  const c = setup(t)
  const input = c.element({ native: true })
  c.move(c.element({ interactive: true })); c.tick()
  c.doc.emit('focusin', { target: input }); c.tick()
  assert.equal(c.styles[0].textContent, '')
  c.move(c.element({ interactive: true })); c.tick()
  c.win.emit('pointerdown', { pointerType: 'mouse', target: input }); c.tick()
  assert.equal(c.styles[0].textContent, '')
})
