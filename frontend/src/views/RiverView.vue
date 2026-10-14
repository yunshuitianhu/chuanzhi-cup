<script setup>
/**
 * 河道模型页 —— 对应 app.py 的 tab3。
 *
 * 后端接口（/api/river/*）尚未接入，此页先给出结构占位，
 * 避免出现"看似能用但点不动"的假象 —— 按钮禁用并说明原因。
 */
import Panel from '../components/Panel.vue'
</script>

<template>
  <Panel title="水动力-水质模型" subtitle="一维 Saint-Venant 方程 + ADR 对流扩散反应模型">
    <div class="notice">
      <div class="ico">🚧</div>
      <h3>该模块正在接入后端</h3>
      <p>
        前端页面结构已就位，后端 <code>/api/river/*</code> 接口尚未实现。
      </p>
      <p class="warn">
        <strong>另需注意：</strong>该模型的非恒定（潮汐）工况存在一个已确认的
        数值缺陷 —— 连续方程遗漏水面宽，导致质量不守恒。详见
        <code>docs/代码审计与修改方案.md</code> 第 2.8 节。
        作品说明中<strong>不应把「潮汐模拟」作为宣传点</strong>。
      </p>
    </div>
  </Panel>
</template>

<style scoped>
.notice { text-align: center; padding: 50px 24px; color: var(--c-sub); }
.ico { font-size: 38px; margin-bottom: 10px; }
h3 { margin: 0 0 10px; font-size: 15px; color: var(--c-ink); }
p { margin: 6px 0; font-size: 13px; line-height: 1.8; }
code { background: #0d151d; padding: 1px 5px; border-radius: 3px; font-size: 12px; }
.warn {
  max-width: 620px; margin: 16px auto 0; text-align: left;
  background: #2a1e10; border: 1px solid #6b4a10; border-radius: 8px;
  padding: 12px 16px; color: #e8c98a; font-size: 12px;
}
</style>
