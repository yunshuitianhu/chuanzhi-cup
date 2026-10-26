/**
 * @typedef {{ scrollTop: number, scrollHeight: number, clientHeight: number }} ScrollElement
 * @typedef {{ following: boolean, hasUnseen: boolean }} ChatScrollState
 *
 * One writer per animation frame. Growth of the content never by itself turns
 * following off; only reader interaction or an active text selection pauses it.
 * The view should schedule after DOM updates (e.g. Vue nextTick).
 *
 * @param {{
 *   getElement: () => ScrollElement | null,
 *   hasSelection?: () => boolean,
 *   onChange?: (state: ChatScrollState) => void,
 *   request?: (callback: FrameRequestCallback) => number,
 *   cancel?: (handle: number) => void,
 *   bottomThreshold?: number
 * }} options
 */
export function createChatScroll({
  getElement,
  hasSelection = () => false,
  onChange = () => {},
  request = requestAnimationFrame,
  cancel = cancelAnimationFrame,
  bottomThreshold = 48,
}) {
  const threshold = Number.isFinite(bottomThreshold) ? Math.max(0, bottomThreshold) : 48
  /** @type {number | null} */
  let frame = null
  let disposed = false, following = true, hasUnseen = false, explicitJump = false
  /** @type {number | null} */
  let writtenTop = null
  let lastTop = 0, lastHeight = 0

  /** @param {boolean} nextFollowing @param {boolean} nextUnseen */
  function update(nextFollowing, nextUnseen) {
    if (disposed || (nextFollowing === following && nextUnseen === hasUnseen)) return
    following = nextFollowing
    hasUnseen = nextUnseen
    onChange({ following, hasUnseen })
  }

  function pause() {
    if (disposed) return
    explicitJump = false
    writtenTop = null
    update(false, hasUnseen)
  }

  function flush() {
    frame = null
    if (disposed) return
    const element = getElement()
    if (!element) return
    const forced = explicitJump
    explicitJump = false
    if ((!following || hasSelection()) && !forced) {
      update(false, true)
      return
    }
    const target = Math.max(0, element.scrollHeight - element.clientHeight)
    // No smooth animation: a continuously moving target must not fight the reader.
    if (Math.abs(element.scrollTop - target) > 1) {
      element.scrollTop = target
      writtenTop = element.scrollTop
    } else {
      writtenTop = null
    }
    lastTop = element.scrollTop
    lastHeight = element.scrollHeight
    update(true, false)
  }

  function schedule() {
    if (disposed) return
    if (!explicitJump && (!following || hasSelection())) {
      update(false, true)
      return
    }
    if (frame === null) frame = request(flush)
  }

  function jumpToLatest() {
    if (disposed) return
    explicitJump = true
    update(true, false)
    schedule()
  }

  function onScroll() {
    if (disposed) return
    const element = getElement()
    if (!element) return
    const top = Math.max(0, element.scrollTop)
    const height = element.scrollHeight
    const changedHeight = height !== lastHeight
    const movingUp = top < lastTop - 1
    const ownScroll = writtenTop !== null && Math.abs(top - writtenTop) <= 1
    writtenTop = null
    lastTop = top
    lastHeight = height
    // The event produced by an explicit jump must not immediately undo it.
    // A real new selection should call pause() from the view's selectionchange.
    if (ownScroll) return
    if (hasSelection()) {
      update(false, hasUnseen)
      return
    }
    // Layout/scroll anchoring may emit scroll as streamed content grows.
    // Explicit wheel/key/touch reading should call pause() before this event.
    if (following && changedHeight) return
    const gap = Math.max(0, height - element.clientHeight - top)
    if (gap <= threshold && !movingUp) update(true, false)
    else update(false, hasUnseen)
  }

  function dispose() {
    if (disposed) return
    disposed = true
    if (frame !== null) cancel(frame)
    frame = null
    explicitJump = false
    writtenTop = null
  }

  return {
    schedule, onScroll, pause, jumpToLatest, dispose,
    get state() { return { following, hasUnseen } },
  }
}
