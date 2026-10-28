<script setup>
defineProps({ diagnostics: { type: Object, default: null } })
function fmt(value) { return Number.isFinite(value) ? value.toFixed(3) : '—' }
</script>

<template>
  <details v-if="diagnostics?.requested_dt_s !== undefined" class="numerical-resolution">
    <summary>数值分辨率与计算限制</summary>
    <p v-if="diagnostics.hydro_grid_nodes != null">水动力断面 {{ diagnostics.hydro_grid_nodes }} 个；水质网格每段细分 {{ diagnostics.wq_refinement }} 倍，共 {{ diagnostics.grid_nodes }} 个计算节点。</p>
    <p v-else>本结果记录 {{ diagnostics.grid_nodes ?? '—' }} 个计算节点，未区分水动力与水质网格。</p>
    <p>水质网格间距 {{ fmt(diagnostics.min_spacing_m) }}–{{ fmt(diagnostics.max_spacing_m) }} m。细分使用原水动力面积与流量插值，未增加实测断面信息。</p>
    <p>请求步长 {{ fmt(diagnostics.requested_dt_s) }} s；实际输移子步 {{ fmt(diagnostics.actual_min_dt_s) }}–{{ fmt(diagnostics.actual_max_dt_s) }} s，共 {{ diagnostics.transport_substeps ?? '—' }} 步。</p>
    <p>实际步长还受稳定性、输出时刻与排放边界约束，修改请求步长后应核对实际子步是否变化。</p>
    <p>经验弥散系数范围 {{ fmt(diagnostics.dispersion_min_m2s) }}–{{ fmt(diagnostics.dispersion_max_m2s) }} m²/s，仍需按当前河段率定。</p>
    <p>当前迎风输移可能摊宽污染峰。建议选择更细水质网格复算并对比峰值、到达时间；质量守恒不能代替精度验证。</p>
  </details>
  <p v-else class="legacy-numerics">本结果未记录数值分辨率；如需查看实际积分子步，请重新运行水质模拟。</p>
</template>

<style scoped>
.numerical-resolution, .legacy-numerics { font-size: 14px; color: var(--c-sub); line-height: 1.75; margin: 12px 0; }
summary { cursor: pointer; }
</style>
