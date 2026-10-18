import L from 'leaflet'
import { severityRaster } from './severityHeat'

export function severityLayer(points) {
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
      const size = this.map.getSize(), scale = 4
      const width = Math.ceil(size.x/scale), height = Math.ceil(size.y/scale)
      if (!width || !height) return
      const canvas = this.canvas
      canvas.width = width; canvas.height = height
      canvas.style.width = `${width*scale}px`; canvas.style.height = `${height*scale}px`
      L.DomUtil.setPosition(canvas, this.map.containerPointToLayerPoint([0, 0]))
      const projected = points.map(([lat, lon, score]) => {
        const p = this.map.latLngToContainerPoint([lat, lon])
        return [p.x/scale, p.y/scale, score]
      })
      const context = canvas.getContext('2d')
      const img = context.createImageData(width, height)
      img.data.set(severityRaster(projected, width, height))
      context.putImageData(img, 0, 0)
    },
  })
  return new Layer()
}
