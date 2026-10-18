// 静态接口最大1024px；超宽窗口降低请求级别，等比例显示并保持地理范围。
export function staticViewport(width, height, zoom) {
  const reduction = Math.min(zoom-1, Math.max(0, Math.ceil(Math.log2(Math.max(width,height)/1024))))
  const scale = 2**reduction
  const w = Math.min(1024, Math.max(1, Math.ceil(width/scale)))
  const h = Math.min(1024, Math.max(1, Math.ceil(height/scale)))
  return { zoom:zoom-reduction, width:w, height:h, displayWidth:w*scale, displayHeight:h*scale }
}
