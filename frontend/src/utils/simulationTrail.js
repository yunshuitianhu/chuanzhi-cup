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

// Model-node intervals are independent of whether geographic registration is available.
export function trailIntervals(sim, frameIndex, profile = trailProfile(sim)) {
  const time = sim.times?.[frameIndex], row = sim.concentrations?.[frameIndex]
  if (!Array.isArray(row) || !Number.isFinite(time) || frameIndex === 0 ||
      time <= (sim.source?.source_start_h ?? 0) || profile.max <= profile.threshold) return []
  const delta = row.map((value, i) => Number.isFinite(value) && Number.isFinite(profile.initial[i])
    ? Math.max(0, profile.sign * (value - profile.initial[i])) : null)
  const segments = []
  for (let i = 1; i < (sim.chainage?.length ?? row.length); i++) {
    const a = delta[i - 1], b = delta[i]
    if (a == null || b == null || Math.max(a, b) <= profile.threshold) continue
    // Clip spatial interpolation to the visible increment; don't paint a whole zero node interval.
    const lo = a <= profile.threshold ? (profile.threshold - a) / (b - a) : 0
    const hi = b <= profile.threshold ? (a - profile.threshold) / (a - b) : 1
    segments.push({ index: i, lo, hi,
      a: (a + (b - a) * lo) / profile.max, b: (a + (b - a) * hi) / profile.max })
  }
  return segments
}

export function trailSegments(sim, frameIndex, profile = trailProfile(sim)) {
  if (!sim.mapping?.available) return []
  const positions = sim.mapping.positions || []
  const valid = p => Array.isArray(p) && p.length === 2 && p.every(Number.isFinite) && Math.abs(p[0]) <= 90 && Math.abs(p[1]) <= 180
  return trailIntervals(sim, frameIndex, profile).flatMap(s => {
    const from = positions[s.index - 1], to = positions[s.index]
    if (!valid(from) || !valid(to)) return []
    const coord = t => from.map((x, k) => x + (to[k] - x) * t)
    return [{ from: coord(s.lo), to: coord(s.hi), a: s.a, b: s.b }]
  })
}

export function peakTrailFrame(sim, profile = trailProfile(sim)) {
  let frame = 0, peak = 0
  for (let i = 1; i < (sim.times?.length || 0); i++) {
    for (const s of trailIntervals(sim, i, profile)) {
      const value = Math.max(s.a, s.b)
      if (value > peak) { peak = value; frame = i }
    }
  }
  return frame
}

export function trailColor(strength, alpha = 1) {
  const t = Math.max(0, Math.min(1, strength))
  const low = [111, 35, 107], mid = [244, 63, 94], high = [255, 222, 213]
  const [a, b, u] = t < .65 ? [low, mid, t / .65] : [mid, high, (t - .65) / .35]
  return `rgba(${a.map((v, i) => Math.round(v + (b[i] - v) * u)).join(',')},${alpha})`
}
