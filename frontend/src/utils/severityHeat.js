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

// 坐标是低分辨率画布像素。核内求平均，避免重复采样把同一浓度累加为深红。
export function severityRaster(points, width, height, radius = 7) {
  const sums = new Float64Array(width * height), weights = new Float64Array(width * height)
  const support = new Float32Array(width * height)
  for (const [x, y, value] of points) {
    if (![x, y, value].every(Number.isFinite) || value < 0) continue
    for (let j = Math.max(0, Math.floor(y-radius)); j <= Math.min(height-1, Math.ceil(y+radius)); j++) {
      for (let i = Math.max(0, Math.floor(x-radius)); i <= Math.min(width-1, Math.ceil(x+radius)); i++) {
        const d2 = ((i-x)**2 + (j-y)**2) / radius**2
        if (d2 >= 1) continue
        const w = (1 - d2)**2, k = j*width+i
        sums[k] += w*value; weights[k] += w; support[k] = Math.max(support[k], w)
      }
    }
  }
  const pixels = new Uint8ClampedArray(width*height*4)
  for (let k = 0; k < weights.length; k++) {
    if (!weights[k]) continue
    pixels.set(severityColor(sums[k]/weights[k]), k*4)
    pixels[k*4+3] = Math.round(220*Math.min(1, support[k]*3))
  }
  return pixels
}
