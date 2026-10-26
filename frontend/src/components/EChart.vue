<script setup>
/**
 * ECharts 封装组件。
 *
 * 三个必须处理的问题（否则图表会出各种毛病）：
 *   1. **容器尺寸为 0 时初始化会得到空白图** —— 用 ResizeObserver 在拿到
 *      真实尺寸后再 init。
 *   2. **组件卸载必须 dispose** —— 否则切换路由会累积实例与事件监听。
 *   3. **option 更新要走 setOption 而不是重建实例** —— 重建会丢动画与交互状态。
 */
import { onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import * as echarts from 'echarts/core'
import { BarChart, LineChart, ScatterChart, HeatmapChart } from 'echarts/charts'
import {
  GridComponent, TooltipComponent, LegendComponent, MarkLineComponent,
  VisualMapComponent, DataZoomComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { frameScheduler } from '../utils/frameScheduler'

echarts.use([
  BarChart, LineChart, ScatterChart, HeatmapChart,
  GridComponent, TooltipComponent, LegendComponent, MarkLineComponent,
  VisualMapComponent, DataZoomComponent, CanvasRenderer,
])

const props = defineProps({
  option: { type: Object, required: true },
  height: { type: [Number, String], default: 320 },
  loading: { type: Boolean, default: false },
})

const emit=defineEmits(['rangeSelected'])
const el = ref(null)
const chart = shallowRef(null)   // shallowRef：ECharts 实例不需要深层响应式
let ro = null
let observer = null, frames = null, visible = false, dirty = true

function ensure() {
  if (!visible || chart.value || !el.value) return
  if (el.value.clientWidth === 0 || el.value.clientHeight === 0) return  // 尺寸未就绪
  chart.value = echarts.init(el.value, 'dark', { renderer: 'canvas' })
  chart.value.on('datazoom', event=>emit('rangeSelected',event))
  chart.value.setOption(props.option)
  dirty = false
  if (props.loading) chart.value.showLoading('default', { text: '加载中', color: '#2ea3f2' })
}

onMounted(() => {
  frames = frameScheduler(() => {
    if (!visible) return
    ensure()
    if (chart.value && dirty) { chart.value.setOption(props.option, true); dirty=false }
    chart.value?.resize()
    if (props.loading) chart.value?.showLoading('default', { text:'处理中', color:'#2ea3f2' })
    else chart.value?.hideLoading()
  })
  if (typeof IntersectionObserver === 'undefined') { visible=true; frames.schedule() }
  else {
    observer = new IntersectionObserver(entries => {
      visible = entries[0]?.isIntersecting || false
      if (visible) frames.schedule()
    }, { rootMargin:'200px' })
    observer.observe(el.value)
  }
  ro = new ResizeObserver(() => {
    frames.schedule()
  })
  if (el.value) ro.observe(el.value)
})

onBeforeUnmount(() => {
  frames?.dispose(); observer?.disconnect()
  ro?.disconnect()
  ro = null
  chart.value?.dispose()
  chart.value = null
})

watch(() => props.option, () => {
  dirty = true
  // notMerge=true：系列数量变化时（如切数据）必须整体替换，否则残留旧系列
  frames?.schedule()
})

watch(() => props.loading, () => {
  frames?.schedule()
})
</script>

<template>
  <div
    ref="el"
    class="echart"
    :style="{ height: typeof height === 'number' ? height + 'px' : height }"
  ></div>
</template>

<style scoped>
.echart { width:100%; min-width:0; }
</style>
