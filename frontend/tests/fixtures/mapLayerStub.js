// 只用于无DOM的图层生命周期检查；不模拟浏览器视觉结果。
export const images = []
class Layer {
  static extend(properties) { class Custom extends Layer {} Object.assign(Custom.prototype, properties); return Custom }
}
export const point = (x,y) => ({x,y,subtract:p=>point(x-p.x,y-p.y),add:p=>point(x+p.x,y+p.y)})
export default {
  Layer, point, latLngBounds:(a,b)=>[a,b],
  imageOverlay(url,bounds,options) {
    const events = {}
    const image = {
      url,bounds,opacity:options.opacity,removed:false,
      once(name,fn) {events[name]=fn;return this},
      addTo(map) {this.map=map;images.push(this);return this},
      emit(name) {events[name]?.()},
      remove() {this.removed=true},
      setOpacity(value) {this.opacity=value},
    }
    return image
  },
}
