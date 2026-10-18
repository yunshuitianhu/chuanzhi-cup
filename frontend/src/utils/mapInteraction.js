// 色带点击命中真实采样点；不把任意地图位置伪装成实测。
export function nearestSamples(samples, point, project, radius = 26) {
  const hits = []
  for (const sample of samples || []) {
    const p = project(sample.coord)
    const distance = Math.hypot(p.x-point.x, p.y-point.y)
    if (Number.isFinite(distance) && distance <= radius) hits.push({sample,distance})
  }
  hits.sort((a,b)=>a.distance-b.distance || a.sample.id-b.sample.id)
  if (!hits.length) return []
  return hits.filter(h=>h.distance<=hits[0].distance+3).map(h=>h.sample.id)
}

export const sampleMetrics = [
  ['ammonia_nitrogen','氨氮','mg/L'], ['dissolved_oxygen','溶解氧','mg/L'],
  ['cod','COD','mg/L'], ['turbidity','浊度','NTU'], ['ph','pH',''],
]
export function numberText(value, digits = 3) {
  return value !== null && value !== undefined && Number.isFinite(Number(value))
    ? Number(value).toLocaleString('zh-CN',{maximumFractionDigits:digits}) : '—'
}
