import { severityColor } from './severityHeat.js'

export function simulationColor(value, scale) {
  const span = scale.max-scale.min
  let t = span > 0 ? (value-scale.min)/span : .5
  if (scale.inverse) t = 1-t
  return `rgb(${severityColor(Math.max(0,Math.min(1,t))*8).join(',')})`
}

// 可注入时钟，验证暂停、结尾和卸载；每个播放器持有自己的计时器。
export function playbackClock(step, {set = setInterval, clear = clearInterval} = {}) {
  let timer = null
  return {
    start(speed = 1) { this.stop(); timer=set(step,500/speed) },
    stop() { if (timer !== null) clear(timer); timer=null },
  }
}
