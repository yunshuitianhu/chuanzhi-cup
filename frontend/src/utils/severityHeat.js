// 颜色刻度固定于污染指数，不随本次样本最大值归一化。
const STOPS = [[0, [23,160,92]], [1, [88,211,82]], [2, [255,217,30]],
  [3, [255,123,24]], [5, [239,40,37]], [8, [172,0,45]]]
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
export function severityRaster(points, width, height, radius = 26, opacity = .94) {
  if (!Number.isFinite(radius) || radius <= 0) throw new RangeError('radius must be positive')
  if (!Number.isFinite(opacity) || opacity < 0 || opacity > 1) throw new RangeError('opacity must be between 0 and 1')
  const sums = new Float64Array(width * height), weights = new Float64Array(width * height)
  const nearest = new Float32Array(width * height).fill(1)
  for (const [x, y, value] of points) {
    if (![x, y, value].every(Number.isFinite) || value < 0) continue
    for (let j = Math.max(0, Math.floor(y-radius)); j <= Math.min(height-1, Math.ceil(y+radius)); j++) {
      for (let i = Math.max(0, Math.floor(x-radius)); i <= Math.min(width-1, Math.ceil(x+radius)); i++) {
        const d2 = ((i-x)**2 + (j-y)**2) / radius**2
        if (d2 >= 1) continue
        const w = Math.exp(-4.5*d2)*(1-d2)**2, k = j*width+i
        sums[k] += w*Math.min(8,value); weights[k] += w; nearest[k] = Math.min(nearest[k], d2)
      }
    }
  }
  const pixels = new Uint8ClampedArray(width*height*4)
  for (let k = 0; k < weights.length; k++) {
    if (!weights[k]) continue
    pixels.set(severityColor(sums[k]/weights[k]), k*4)
    // 中心清晰，外围以零斜率淡出；不因重复采样提高不透明度。
    const edge = Math.max(0, Math.min(1, (Math.sqrt(nearest[k])-.2)/.8))
    pixels[k*4+3] = Math.round(255*opacity*(1-edge*edge*(3-2*edge)))
  }
  return pixels
}
