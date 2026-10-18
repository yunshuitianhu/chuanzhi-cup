import L from 'leaflet'
import { staticViewport } from './staticViewport'

// 每个视口只请求一张官方静态地图。缩放/平移结束后更新，旧请求及Blob及时释放。
export function amapStaticLayer(fetchImage, status) {
  return new (L.Layer.extend({
    options: { attribution:'&copy; <a href="https://www.amap.com/" target="_blank" rel="noopener">高德地图</a>' },
    onAdd(map) {
      this.map = map
      this.schedule = () => {
        clearTimeout(this.timer)
        this.controller?.abort()
        this.timer = setTimeout(() => this.refresh(), 180)
      }
      map.on('moveend zoomend resize', this.schedule)
      this.schedule()
    },
    async refresh() {
      const map = this.map
      if (!map) return
      const size = map.getSize()
      if (!size.x || !size.y) return
      const spec = staticViewport(size.x, size.y, map.getZoom())
      const c = map.getCenter()
      const lat = Number(c.lat.toFixed(6)), lon = Number(c.lng.toFixed(6))
      const center = map.project([lat,lon], map.getZoom())
      const half = L.point(spec.displayWidth/2, spec.displayHeight/2)
      const bounds = L.latLngBounds(map.unproject(center.subtract(half)), map.unproject(center.add(half)))
      const controller = new AbortController()
      this.controller = controller
      let pending = null, objectUrl = null
      status('loading', '正在加载高德底图…')
      try {
        const blob = await fetchImage({lat, lon, zoom:spec.zoom, width:spec.width, height:spec.height}, controller.signal)
        if (controller.signal.aborted || this.map !== map) return
        objectUrl = URL.createObjectURL(blob)
        pending = L.imageOverlay(objectUrl, bounds, {pane:'tilePane', interactive:false, opacity:0})
        await new Promise((resolve,reject) => {
          pending.once('load', resolve).once('error', () => reject(new Error('地图图片解码失败')))
          controller.signal.addEventListener('abort', () => reject(new DOMException('Aborted','AbortError')), {once:true})
          pending.addTo(map)
        })
        if (controller.signal.aborted || this.map !== map) return
        this.overlay?.remove()
        if (this.url) URL.revokeObjectURL(this.url)
        this.overlay = pending; this.url = objectUrl
        pending = null; objectUrl = null
        this.overlay.setOpacity(1)
        status('ready', '高德底图已加载 · 轨迹已对齐')
      } catch(e) {
        if (e.name !== 'AbortError' && this.map === map) status('error', e.message)
      } finally {
        pending?.remove()
        if (objectUrl) URL.revokeObjectURL(objectUrl)
      }
    },
    onRemove(map) {
      this.map = null
      clearTimeout(this.timer)
      this.controller?.abort()
      map.off('moveend zoomend resize', this.schedule)
      this.overlay?.remove(); this.overlay = null
      if (this.url) URL.revokeObjectURL(this.url)
      this.url = null
    },
  }))()
}
