import L from 'leaflet'
import { severityRaster } from './severityHeat'

export function severityLayer(points, { radius = 26, opacity = .94 } = {}) {
  const Layer = L.Layer.extend({
    onAdd(map) {
      this.map = map
      this.canvas = L.DomUtil.create('canvas', 'severity-heat', map.getPanes().overlayPane)
      this.canvas.style.pointerEvents = 'none'
      map.on('moveend zoomend resize', this.redraw, this)
      this.redraw()
    },
    onRemove(map) {
      map.off('moveend zoomend resize', this.redraw, this)
      this.canvas.remove()
    },
    redraw() {
      const size = this.map.getSize(), scale = 1
      const width = Math.ceil(size.x/scale), height = Math.ceil(size.y/scale)
      if (!width || !height) return
      const canvas = this.canvas
      const ratio = Math.min(window.devicePixelRatio || 1, 2)
      canvas.width = Math.round(size.x*ratio); canvas.height = Math.round(size.y*ratio)
      canvas.style.width = `${size.x}px`; canvas.style.height = `${size.y}px`
      L.DomUtil.setPosition(canvas, this.map.containerPointToLayerPoint([0, 0]))
      const projected = points.map(([lat, lon, score]) => {
        const p = this.map.latLngToContainerPoint([lat, lon])
        return [p.x/scale, p.y/scale, score]
      })
      const source = document.createElement('canvas')
      source.width = width; source.height = height
      const sourceContext = source.getContext('2d')
      const img = sourceContext.createImageData(width, height)
      img.data.set(severityRaster(projected, width, height, radius/scale, opacity))
      sourceContext.putImageData(img, 0, 0)
      const context = canvas.getContext('2d')
      context.imageSmoothingEnabled = true
      context.imageSmoothingQuality = 'high'
      context.drawImage(source, 0, 0, width, height, 0, 0, width*scale*ratio, height*scale*ratio)
    },
  })
  return new Layer()
}
