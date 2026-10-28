<script setup>
defineProps({ summary: { type: Object, default: null } })
</script>

<template>
  <section v-if="summary" class="quality-summary" aria-label="传感器质控摘要">
    <div class="quality-metrics">
      <div><span>原始阈值预警</span><b>{{ summary['原始阈值预警点数'] ?? '—' }}</b></div>
      <div><span>预警中需质控复核</span><b>{{ summary['需质控复核预警点数'] ?? '—' }}</b></div>
      <div><span>全部需复核记录</span><b>{{ summary['需复核记录数'] ?? '—' }}</b></div>
      <div><span>关键指标缺测</span><b>{{ summary['关键指标缺测记录数'] ?? '—' }}</b></div>
    </div>
    <p>复核标记不撤销阈值预警，也不能确认仪器故障；未标记不等于仪器合格或已确认污染。</p>
    <details>
      <summary>检查口径、阈值与原值保留情况</summary>
      <p>{{ summary['检查范围'] }}</p>
      <p v-if="summary['参数']">突刺差值门限：氨氮 {{ summary['参数'].spike_thresholds?.ammonia_nitrogen }} mg/L，DO {{ summary['参数'].spike_thresholds?.dissolved_oxygen }} mg/L；恒值段至少 {{ summary['参数'].flat_duration_s }} 秒、{{ summary['参数'].flat_min_points }} 个独立时刻；不跨 {{ summary['参数'].max_gap_s }} 秒以上空档。</p>
      <p v-if="summary['参数']">恒值容差：氨氮 {{ summary['参数'].flat_tolerances?.ammonia_nitrogen }} mg/L，DO {{ summary['参数'].flat_tolerances?.dissolved_oxygen }} mg/L。以上为工程配置，尚未按仪器率定。</p>
      <p v-if="summary['原始值已保留']">原值单独保存：氨氮 {{ summary['原始值已保留'].ammonia_nitrogen ? '是' : '否（旧处理值不能当原值）' }}，DO {{ summary['原始值已保留'].dissolved_oxygen ? '是' : '否（旧处理值不能当原值）' }}。</p>
      <p>输入无效 {{ summary['输入无效记录数'] ?? '—' }} 条；时序上下文不足 {{ summary['上下文不足记录数'] ?? '—' }} 条。</p>
      <ul><li v-for="(count, rule) in summary['规则标记记录数']" :key="rule">{{ rule }}：{{ count }} 条</li></ul>
      <p>{{ summary['局限'] }}</p>
    </details>
  </section>
</template>

<style scoped>
.quality-summary { margin: 12px 0; padding: 14px; border: 1px solid var(--c-border); border-radius: 9px; background: var(--c-surface-2); }
.quality-metrics { display: grid; grid-template-columns: repeat(auto-fit,minmax(130px,1fr)); gap: 12px; }
.quality-metrics span { display: block; color: var(--c-sub); font-size: 14px; }
.quality-metrics b { display: block; font-size: 26px; color: var(--c-accent); margin-top: 4px; }
p, li, summary { font-size: 14px; line-height: 1.65; color: var(--c-sub); }
summary { cursor: pointer; }
</style>
