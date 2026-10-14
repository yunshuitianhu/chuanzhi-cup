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
import * as echarts from 'echarts'

const props = defineProps({
  option: { type: Object, required: true },
  height: { type: [Number, String], default: 320 },
  loading: { type: Boolean, default: false },
})

const el = ref(null)
const chart = shallowRef(null)   // shallowRef：ECharts 实例不需要深层响应式
let ro = null

function ensure() {
  if (chart.value || !el.value) return
  if (el.value.clientWidth === 0 || el.value.clientHeight === 0) return  // 尺寸未就绪
  chart.value = echarts.init(el.value, 'dark', { renderer: 'canvas' })
  chart.value.setOption(props.option)
  if (props.loading) chart.value.showLoading('default', { text: '加载中', color: '#2ea3f2' })
}

onMounted(() => {
  ensure()
  ro = new ResizeObserver(() => {
    ensure()
    chart.value?.resize()
  })
  if (el.value) ro.observe(el.value)
})

onBeforeUnmount(() => {
  ro?.disconnect()
  ro = null
  chart.value?.dispose()
  chart.value = null
})

watch(() => props.option, (opt) => {
  if (!chart.value) { ensure(); return }
  // notMerge=true：系列数量变化时（如切数据）必须整体替换，否则残留旧系列
  chart.value.setOption(opt, true)
}, { deep: true })

watch(() => props.loading, (v) => {
  if (!chart.value) return
  if (v) chart.value.showLoading('default', { text: '处理中', color: '#2ea3f2' })
  else chart.value.hideLoading()
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
.echart { width: 100%; }
</style>
