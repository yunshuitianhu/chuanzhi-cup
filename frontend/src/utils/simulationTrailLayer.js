import L from 'leaflet'
import { frameScheduler } from './frameScheduler'
import { trailColor } from './simulationTrail'

// Pixel Trail-inspired rounded gel/glow appearance; fixed width is a display parameter.
export function simulationTrailLayer(segments) {
  const Layer = L.Layer.extend({
    onAdd(map) {
      this.map = map
      this.canvas = L.DomUtil.create('canvas', 'simulation-trail', map.getPanes().overlayPane)
      this.canvas.style.pointerEvents = 'none'
      this.frames = frameScheduler(() => this.draw())
      map.on('move zoom resize', this.redraw, this)
      this.redraw()
    },
    onRemove(map) {
      map.off('move zoom resize', this.redraw, this)
      this.frames.dispose()
      this.canvas.remove()
    },
    setSegments(next) { segments = next; this.redraw(); return this },
    redraw() { this.frames?.schedule() },
    draw() {
      const size = this.map.getSize(), ratio = Math.min(window.devicePixelRatio || 1, 2)
      if (size.x <= 0 || size.y <= 0) return
      const canvas = this.canvas
      canvas.width = Math.round(size.x * ratio); canvas.height = Math.round(size.y * ratio)
      canvas.style.width = `${size.x}px`; canvas.style.height = `${size.y}px`
      L.DomUtil.setPosition(canvas, this.map.containerPointToLayerPoint([0, 0]))
      const ctx = canvas.getContext('2d')
      if (!ctx) return
      ctx.scale(ratio, ratio)
      ctx.lineCap = 'round'; ctx.lineJoin = 'round'
      const projected = segments.map(s => ({ ...s,
        from: this.map.latLngToContainerPoint(s.from), to: this.map.latLngToContainerPoint(s.to) }))
      // Broad low-alpha edge, gel core and narrow specular highlight; no historical accumulation.
      for (const [width, opacity] of [[28, .08], [20, .2], [12, .85], [3, .65]]) {
        for (const s of projected) {
          if (Math.hypot(s.from.x - s.to.x, s.from.y - s.to.y) < .01) continue
          const gradient = ctx.createLinearGradient(s.from.x, s.from.y, s.to.x, s.to.y)
          const alpha = t => opacity * Math.pow(Math.max(0, Math.min(1, t)), .45)
          gradient.addColorStop(0, trailColor(width === 3 ? Math.max(.8, s.a) : s.a, alpha(s.a)))
          gradient.addColorStop(1, trailColor(width === 3 ? Math.max(.8, s.b) : s.b, alpha(s.b)))
          ctx.strokeStyle = gradient; ctx.lineWidth = width
          ctx.beginPath(); ctx.moveTo(s.from.x, s.from.y); ctx.lineTo(s.to.x, s.to.y); ctx.stroke()
        }
      }
    },
  })
  return new Layer()
}
