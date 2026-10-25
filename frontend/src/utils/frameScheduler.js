// One task per animation frame; disposal prevents callbacks on removed components.
/**
 * @param {() => void} task
 * @param {(callback: FrameRequestCallback) => number} request
 * @param {(handle: number) => void} cancel
 */
export function frameScheduler(task, request = requestAnimationFrame, cancel = cancelAnimationFrame) {
  /** @type {number | null} */
  let frame = null
  let disposed = false
  return {
    schedule() {
      if (disposed || frame !== null) return
      frame = request(() => { frame = null; if (!disposed) task() })
    },
    dispose() { disposed = true; if (frame !== null) cancel(frame); frame = null },
  }
}
