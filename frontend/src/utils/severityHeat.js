// 颜色刻度固定于污染指数，不随本次样本最大值归一化。
const STOPS = [[0, [46,139,87]], [1, [102,190,90]], [2, [255,200,60]],
  [3, [255,115,35]], [5, [220,35,60]], [8, [120,0,35]]]
export function severityColor(score) {
  const s = Math.max(0, Math.min(8, score))
  const hi = STOPS.findIndex(([v]) => v >= s)
  if (hi <= 0) return STOPS[0][1]
  const [a, ca] = STOPS[hi - 1], [b, cb] = STOPS[hi]
  const f = (s - a) / (b - a)
  return ca.map((v, i) => Math.round(v + f * (cb[i] - v)))
}

// 坐标、半径使用同一画布像素单位。有限支撑高斯核只作显示插值，不补造观测。
// 先按固定色标截顶再平均：>=8 本就同色，极端值不应把整片邻域压成最高色阶。
export function severityRaster(points, width, height, radius = 24) {
  if (!Number.isFinite(radius) || radius <= 0) throw new RangeError('radius must be positive')
  const sums = new Float64Array(width * height), weights = new Float64Array(width * height)
  const support = new Float32Array(width * height)
  for (const [x, y, value] of points) {
    if (![x, y, value].every(Number.isFinite) || value < 0) continue
    for (let j = Math.max(0, Math.floor(y-radius)); j <= Math.min(height-1, Math.ceil(y+radius)); j++) {
      for (let i = Math.max(0, Math.floor(x-radius)); i <= Math.min(width-1, Math.ceil(x+radius)); i++) {
        const d2 = ((i-x)**2 + (j-y)**2) / radius**2
        if (d2 >= 1) continue
        const w = Math.exp(-4.5*d2)*(1-d2)**2, k = j*width+i
        sums[k] += w*Math.min(8,value); weights[k] += w; support[k] = Math.max(support[k], w)
      }
    }
  }
  const pixels = new Uint8ClampedArray(width*height*4)
  for (let k = 0; k < weights.length; k++) {
    if (!weights[k]) continue
    pixels.set(severityColor(sums[k]/weights[k]), k*4)
    // 无硬截断的透明度曲线；观测外沿连续淡出，不形成不透明色块的边框。
    pixels[k*4+3] = Math.round(220*Math.sqrt(support[k]))
  }
  return pixels
}
