// 与后端geo_tools一致；只转换地图显示坐标，不修改保存的WGS-84监测数据。
const A = 6378245, EE = .00669342162296594323, PI = Math.PI
const outside = (lat, lon) => !(lat > .8293 && lat < 55.8271 && lon > 72.004 && lon < 137.8347)
export function wgsToGcj([lat, lon]) {
  if (outside(lat, lon)) return [lat, lon]
  const x = lon - 105, y = lat - 35
  const wave = (20*Math.sin(6*x*PI)+20*Math.sin(2*x*PI))*2/3
  let dy = -100+2*x+3*y+.2*y*y+.1*x*y+.2*Math.sqrt(Math.abs(x))+wave
  dy += (20*Math.sin(y*PI)+40*Math.sin(y/3*PI))*2/3
  dy += (160*Math.sin(y/12*PI)+320*Math.sin(y*PI/30))*2/3
  let dx = 300+x+2*y+.1*x*x+.1*x*y+.1*Math.sqrt(Math.abs(x))+wave
  dx += (20*Math.sin(x*PI)+40*Math.sin(x/3*PI))*2/3
  dx += (150*Math.sin(x/12*PI)+300*Math.sin(x/30*PI))*2/3
  const rad = lat/180*PI, magic = 1-EE*Math.sin(rad)**2, root = Math.sqrt(magic)
  return [lat+dy*180/(A*(1-EE)/(magic*root)*PI), lon+dx*180/(A/root*Math.cos(rad)*PI)]
}
export function gcjToWgs([lat, lon]) {
  if (outside(lat, lon)) return [lat, lon]
  let wlat = lat, wlon = lon
  for (let i=0; i<10; i++) {
    const [glat, glon] = wgsToGcj([wlat, wlon])
    const dy = glat-lat, dx = glon-lon
    wlat -= dy; wlon -= dx
    if (Math.max(Math.abs(dy), Math.abs(dx)) < 1e-7) break
  }
  return [wlat, wlon]
}

export function displayTrace(data, useGcj) {
  if (!data || !useGcj) return data
  return {
    ...data,
    track: data.track?.map(wgsToGcj),
    heat: data.heat?.map(([lat, lon, ...rest]) => [...wgsToGcj([lat, lon]), ...rest]),
    clusters: data.clusters?.map(c => ({...c, center:wgsToGcj(c.center)})),
    top5: data.top5?.map(p => ({...p, coord:wgsToGcj(p.coord)})),
    samples: data.samples?.map(p => ({...p, coord:wgsToGcj(p.coord)})),
  }
}
