/**
 * Navigation adaptation of React Bits Rubber Segment by David Haz.
 * Source: https://reactbits.dev/micro/rubber-segment
 * License: ../vendor/react-bits-LICENSE.md (MIT + Commons Clause).
 * Keeps native router links: route completion, not pointer-down, selects the thumb.
 */
const INSET = 4
const RADIUS = 8
const HANDOFF = .15
const SQUASH = 3

function spring(state, goal, dt) {
  const h = dt / 2
  for (let step = 0; step < 2; step++) {
    state.v += (500 * (goal - state.x) - 38 * state.v) * h
    state.x += state.v * h
  }
}

export function mountRubberNavigation(track, thumb, initialValue) {
  let alive = true, raf = 0, previousTime = 0, elapsed = 0, width = 0
  let value = initialValue, slots = [], stage = 'idle', direction = 1
  let origin = [0, 0], expanded = [0, 0], goal = [0, 0]
  const left = { x: 0, v: 0 }, right = { x: 0, v: 0 }
  const motion = window.matchMedia('(prefers-reduced-motion: reduce)')

  function paint() {
    thumb.style.clipPath = `inset(0 ${Math.max(0, width - right.x)}px 0 ${Math.max(0, left.x)}px round ${RADIUS}px)`
    thumb.style.opacity = '1'
    track.dataset.rubberStage = stage
    track.classList.add('rubber-ready')
  }
  function stop() {
    cancelAnimationFrame(raf)
    raf = 0
    previousTime = 0
  }
  function jump(slot) {
    stop()
    left.x = slot.left; right.x = slot.right
    left.v = right.v = 0
    stage = 'idle'
    paint()
  }
  function measure() {
    if (!alive) return
    const rect = track.getBoundingClientRect()
    const links = [...track.querySelectorAll('[data-rubber-value]')]
    slots = links.map(link => {
      const box = link.getBoundingClientRect()
      return { value: link.dataset.rubberValue,
        left: box.left - rect.left - INSET + track.scrollLeft,
        right: box.right - rect.left - INSET + track.scrollLeft }
    })
    width = Math.max(0, track.clientWidth - INSET * 2, ...slots.map(slot => slot.right))
    thumb.style.width = `${width}px`
    const copies = thumb.querySelectorAll('.rubber-copy')
    slots.forEach((slot, i) => {
      if (!copies[i]) return
      copies[i].style.left = `${slot.left}px`
      copies[i].style.width = `${slot.right - slot.left}px`
    })
    const selected = slots.find(slot => slot.value === value)
    if (selected) jump(selected)
    else { stop(); thumb.style.opacity = '0'; track.classList.remove('rubber-ready') }
  }
  function frame(now) {
    raf = 0
    if (!alive) return
    const dt = previousTime ? Math.min(.04, (now - previousTime) / 1000) : 1 / 60
    previousTime = now
    elapsed += dt
    if (stage === 'stretch') {
      const t = Math.min(1, elapsed / HANDOFF)
      const eased = 1 - Math.pow(1 - t, 3)
      left.x = origin[0] + (expanded[0] - origin[0]) * eased
      right.x = origin[1] + (expanded[1] - origin[1]) * eased
      if (t === 1) { stage = 'land'; elapsed = 0 }
    } else {
      const l = goal[0] + (stage === 'land' && direction > 0 ? SQUASH : 0)
      const r = goal[1] - (stage === 'land' && direction < 0 ? SQUASH : 0)
      spring(left, l, dt); spring(right, r, dt)
      const settled = Math.abs(left.x - l) + Math.abs(right.x - r) < .1 &&
        Math.abs(left.v) + Math.abs(right.v) < .3
      if (settled || elapsed > .7) {
        if (stage === 'land') { stage = 'relax'; elapsed = 0 }
        else {
          jump({ left: goal[0], right: goal[1] })
          return
        }
      }
    }
    paint()
    raf = requestAnimationFrame(frame)
  }
  function select(nextValue) {
    if (!alive || nextValue === value) return
    value = nextValue
    const slot = slots.find(item => item.value === value)
    if (!slot) { measure(); return }
    if (motion.matches || document.hidden || !width) { jump(slot); return }
    stop()
    direction = Math.sign(slot.left + slot.right - left.x - right.x) || 1
    origin = [left.x, right.x]
    expanded = [Math.min(left.x, slot.left), Math.max(right.x, slot.right)]
    goal = [slot.left, slot.right]
    left.v = right.v = 0
    stage = 'stretch'; elapsed = 0
    paint()
    raf = requestAnimationFrame(frame)
  }
  function onVisibility() { if (document.hidden) measure() }
  const observer = new ResizeObserver(measure)
  observer.observe(track)
  track.querySelectorAll('[data-rubber-value]').forEach(link => observer.observe(link))
  motion.addEventListener('change', measure)
  document.addEventListener('visibilitychange', onVisibility)
  document.fonts?.ready.then(() => { if (alive) measure() })
  measure()
  return {
    select,
    destroy() {
      alive = false
      stop()
      observer.disconnect()
      motion.removeEventListener('change', measure)
      document.removeEventListener('visibilitychange', onVisibility)
      track.classList.remove('rubber-ready')
      delete track.dataset.rubberStage
      thumb.style.opacity = '0'
    },
  }
}
