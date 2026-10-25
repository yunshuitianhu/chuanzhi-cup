// Controlled CPU benchmark; timings are observations, not portable CI thresholds.
import { performance } from 'node:perf_hooks'
import { severityRaster } from '../src/utils/severityHeat.js'
import { frameScheduler } from '../src/utils/frameScheduler.js'
import { renderReportMarkdown } from '../src/utils/reportMarkdown.js'

const points=Array.from({length:1805},(_,i)=>[450+50*Math.sin(i/150),20+500*i/1804,Math.max(0,3+3*Math.sin(i/210))])
const median=values=>[...values].sort((a,b)=>a-b)[Math.floor(values.length/2)]
function timed(task,runs=9){ task();task();return Array.from({length:runs},()=>{const start=performance.now();task();return performance.now()-start}) }
const raster=()=>severityRaster(points,900,548,26,.94)
const single=timed(raster)
const burst=timed(()=>{raster();raster();raster()})
const merged=timed(()=>{
  let pending
  const scheduler=frameScheduler(raster,fn=>{pending=fn;return 1},()=>{})
  scheduler.schedule();scheduler.schedule();scheduler.schedule();pending();scheduler.dispose()
})
const report=('## 界面受控测试\n\n**DO=0须保留**\n\n| 指标 | 值 |\n|---|---|\n|DO|0|\n\n').repeat(100)
const markdown=timed(()=>renderReportMarkdown(report),15)
console.log(JSON.stringify({
  scope:'Synthetic screen-space workload; not field accuracy, FPS, or cross-device latency',
  points:points.length,canvas:[900,548],radius:26,runs:9,
  raster_ms:{median:median(single),samples:single},
  three_immediate_redraws_ms:{median:median(burst),samples:burst},
  three_scheduled_requests_ms:{median:median(merged),samples:merged},
  report:{characters:report.length,runs:15,median_ms:median(markdown)},
},null,2))
