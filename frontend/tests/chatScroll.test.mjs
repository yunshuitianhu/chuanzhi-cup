import test from 'node:test'
import assert from 'node:assert/strict'
import {createChatScroll} from '../src/utils/chatScroll.js'

function fixture() {
  let top = 0, height = 1000, selection = false, attached = true, writes = 0, serial = 0
  const frames = new Map(), changes = []
  const element = {
    get scrollTop() { return top },
    set scrollTop(value) { writes++; top = Math.max(0, Math.min(value, height - 300)) },
    get scrollHeight() { return height },
    clientHeight: 300,
  }
  const controller = createChatScroll({
    getElement: () => attached ? element : null,
    hasSelection: () => selection,
    onChange: state => changes.push(state),
    request: callback => { const id = ++serial; frames.set(id, callback); return id },
    cancel: id => { frames.delete(id) },
  })
  return {
    controller, element, frames, changes,
    setHeight(value) { height = value },
    select(value = true) { selection = value },
    attach(value) { attached = value },
    scrollTo(value) { top = value; controller.onScroll() },
    flush() { const batch = [...frames.values()]; frames.clear(); for (const callback of batch) callback(0) },
    get top() { return top }, get writes() { return writes },
  }
}

test('many streamed changes share one frame and keep following when content grows', () => {
  const f = fixture()
  f.controller.schedule(); f.controller.schedule(); f.controller.schedule()
  assert.equal(f.frames.size, 1)
  assert.equal(f.writes, 0)
  f.flush()
  assert.equal(f.top, 700); assert.equal(f.writes, 1)
  f.controller.onScroll() // the browser's event from our own write
  f.setHeight(1300)
  f.controller.onScroll() // layout emits an event before the next write
  assert.equal(f.controller.state.following, true)
  f.controller.schedule(); f.controller.schedule(); f.flush()
  assert.equal(f.top, 1000); assert.equal(f.writes, 2)
})

test('reading upwards pauses streaming and reveals unseen content without moving the reader', () => {
  const f = fixture()
  f.controller.jumpToLatest(); f.flush(); f.controller.onScroll()
  f.scrollTo(400)
  assert.equal(f.controller.state.following, false)
  f.setHeight(1200)
  f.controller.schedule(); f.flush()
  assert.equal(f.top, 400); assert.equal(f.writes, 1)
  assert.equal(f.controller.state.hasUnseen, true)
})

test('a small upward user scroll pauses even inside the near-bottom threshold', () => {
  const f = fixture()
  f.controller.jumpToLatest(); f.flush(); f.controller.onScroll()
  f.scrollTo(680)
  assert.equal(f.controller.state.following, false)
  f.controller.schedule(); f.flush()
  assert.equal(f.top, 680)
})

test('explicit reader intent cancels a queued follow even when content height changes concurrently', () => {
  const f = fixture()
  f.controller.jumpToLatest(); f.flush()
  f.setHeight(1500); f.controller.schedule()
  f.controller.pause(); f.scrollTo(650); f.flush()
  assert.equal(f.top, 650); assert.equal(f.writes, 1)
  assert.equal(f.controller.state.following, false)
  assert.equal(f.controller.state.hasUnseen, true)
})

test('text selection pauses before scheduling and again at frame execution time', () => {
  const f = fixture()
  f.controller.jumpToLatest(); f.flush()
  f.select(); f.setHeight(1400); f.controller.schedule(); f.flush()
  assert.equal(f.top, 700); assert.equal(f.controller.state.following, false)
  f.select(false); f.controller.jumpToLatest(); f.flush()
  assert.equal(f.top, 1100)
  f.controller.onScroll()
  f.setHeight(1500); f.controller.schedule(); f.select(); f.flush()
  assert.equal(f.top, 1100); assert.equal(f.controller.state.hasUnseen, true)
})

test('clearing a selection alone never unexpectedly resumes follow; explicit latest does', () => {
  const f = fixture()
  f.controller.jumpToLatest(); f.flush()
  f.select(); f.setHeight(1400); f.controller.schedule()
  f.select(false); f.controller.schedule(); f.flush()
  assert.equal(f.top, 700)
  f.controller.jumpToLatest(); f.flush()
  assert.equal(f.top, 1100)
  assert.deepEqual(f.controller.state, {following: true, hasUnseen: false})
})

test('manual downward scroll back near the bottom resumes following', () => {
  const f = fixture()
  f.controller.jumpToLatest(); f.flush(); f.controller.onScroll()
  f.scrollTo(350)
  f.setHeight(1400); f.controller.schedule()
  f.scrollTo(1090)
  assert.deepEqual(f.controller.state, {following: true, hasUnseen: false})
  f.setHeight(1500); f.controller.schedule(); f.flush()
  assert.equal(f.top, 1200)
})

test('explicit latest is honored as a new user action even while a selection remains', () => {
  const f = fixture()
  f.controller.jumpToLatest(); f.flush()
  f.select(); f.setHeight(1400); f.controller.schedule()
  f.controller.jumpToLatest(); f.flush()
  f.controller.onScroll() // own browser scroll event must not undo the jump
  assert.equal(f.top, 1100); assert.equal(f.controller.state.following, true)
  f.setHeight(1500); f.controller.schedule(); f.flush()
  assert.equal(f.top, 1100) // a still-active selection protects subsequent updates
})

test('missing element is harmless and a later mounted element can still follow', () => {
  const f = fixture()
  f.attach(false); f.controller.schedule(); f.flush()
  assert.equal(f.writes, 0)
  f.attach(true); f.controller.schedule(); f.flush()
  assert.equal(f.top, 700)
})

test('dispose cancels the frame and late calls do not write or notify', () => {
  const f = fixture()
  f.controller.schedule()
  f.controller.dispose()
  assert.equal(f.frames.size, 0)
  const before = f.changes.length
  f.controller.jumpToLatest(); f.controller.pause(); f.controller.onScroll()
  f.controller.schedule(); f.flush(); f.controller.dispose()
  assert.equal(f.writes, 0); assert.equal(f.changes.length, before)
})

test('following an already visible bottom avoids redundant layout writes', () => {
  const f = fixture()
  f.controller.jumpToLatest(); f.flush(); f.controller.onScroll()
  f.controller.schedule(); f.flush()
  assert.equal(f.writes, 1)
  f.setHeight(100); f.controller.schedule(); f.flush()
  assert.equal(f.top, 0)
})
