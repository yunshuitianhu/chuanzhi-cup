// 空输入与0坐标分别处理，防止清空后被Number('')伪造为赤道/本初子午线。
export function traceRequest(lat, lon, radius) {
  const blank = value => value == null || (typeof value === 'string' && !value.trim())
  const latBlank=blank(lat), lonBlank=blank(lon)
  if (latBlank !== lonBlank) return {error:'请同时填写纬度和经度，或将两项均留空自动选择。'}
  if (blank(radius) || !Number.isInteger(Number(radius)) || Number(radius)<50 || Number(radius)>50000)
    return {error:'查询半径须为50至50000米的整数。'}
  const payload={radius_m:Number(radius)}
  if (!latBlank) {
    const a=Number(lat), b=Number(lon)
    if (!Number.isFinite(a) || !Number.isFinite(b) || Math.abs(a)>90 || Math.abs(b)>180)
      return {error:'请输入有效的WGS-84纬度（-90至90）和经度（-180至180）。'}
    payload.lat=a;payload.lon=b
  }
  return {payload,manual:!latBlank}
}
