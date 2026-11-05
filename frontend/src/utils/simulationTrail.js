// Visual support is derived from current model values, never cursor history.
export function trailProfile(sim) {
  const initial = sim.concentrations?.[0] || []
  const sign = sim.constituent === 'do' ? -1 : 1
  let max = 0
  for (const row of sim.concentrations || []) for (let i = 0; i < initial.length; i++) {
    if (!Array.isArray(row)) continue
    if (Number.isFinite(row[i]) && Number.isFinite(initial[i])) max = Math.max(max, sign * (row[i] - initial[i]))
  }
  return { initial, sign, max, threshold: Math.max(1e-8, max * 1e-5) }
}

export function trailSegments(sim, frameIndex, profile = trailProfile(sim)) {
  const time = sim.times?.[frameIndex], row = sim.concentrations?.[frameIndex]
  const positions = sim.mapping?.positions || []
  if (!sim.mapping?.available || !row || !Number.isFinite(time) || frameIndex === 0 ||
      time <= (sim.source?.source_start_h ?? 0) || profile.max <= profile.threshold) return []
  const delta = row.map((value, i) => Number.isFinite(value) && Number.isFinite(profile.initial[i])
    ? Math.max(0, profile.sign * (value - profile.initial[i])) : null)
  const valid = p => Array.isArray(p) && p.length === 2 && p.every(Number.isFinite) && Math.abs(p[0]) <= 90 && Math.abs(p[1]) <= 180
  const segments = []
  for (let i = 1; i < positions.length; i++) {
    const a = delta[i - 1], b = delta[i]
    if (a == null || b == null || !valid(positions[i - 1]) || !valid(positions[i]) ||
      Math.max(a, b) <= profile.threshold) continue
    // Clip spatial interpolation to the visible increment; don't paint a whole zero node interval.
    const lo = a <= profile.threshold ? (profile.threshold - a) / (b - a) : 0
    const hi = b <= profile.threshold ? (a - profile.threshold) / (a - b) : 1
    const coord = t => positions[i - 1].map((x, k) => x + (positions[i][k] - x) * t)
    segments.push({ from: coord(lo), to: coord(hi),
      a: (a + (b - a) * lo) / profile.max, b: (a + (b - a) * hi) / profile.max })
  }
  return segments
}

export function trailColor(strength, alpha = 1) {
  const t = Math.max(0, Math.min(1, strength))
  const low = [111, 35, 107], mid = [244, 63, 94], high = [255, 222, 213]
  const [a, b, u] = t < .65 ? [low, mid, t / .65] : [mid, high, (t - .65) / .35]
  return `rgba(${a.map((v, i) => Math.round(v + (b[i] - v) * u)).join(',')},${alpha})`
}
