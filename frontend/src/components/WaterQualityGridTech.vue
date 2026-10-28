<script setup>
import QuoteText from './QuoteText.vue'
// 已完成的离线解析基准，来源见 docs/水质独立网格与分辨率改进记录.md。
// 这些固定结果不代表当前会话已经模拟，也不代表现场检测准确率。
const benchmarks = [
  { river: '3 km', original: '19.86%', refined: '6.16%', fine: '3.16%' },
  { river: '6 km', original: '18.10%', refined: '5.45%', fine: '2.80%' },
  { river: '12 km', original: '12.24%', refined: '3.36%', fine: '1.71%' },
]
</script>

<template>
  <div class="grid-tech">
    <QuoteText :source="{kind:'help',help_id:'grid_help'}" source-id="grid_help" title="水动力与水质独立网格说明">
    <p class="intro">
      <b>水动力与水质采用独立计算网格</b>，在已有断面之间细分水质单元，缓解粗网格把污染峰摊宽的问题。
      无需增加水动力断面或重新计算水动力，即可比较不同网格下的污染浓度与到达时间。
    </p>

    <div class="workflow" aria-label="独立水质网格计算流程">
      <div class="step">
        <span class="step-label">01 · 水动力输入</span>
        <strong>100 个断面</strong>
        <span>提供流量、过水面积与流速</span>
      </div>
      <div class="step featured">
        <span class="step-label">02 · 独立水质网格</span>
        <strong>397 个计算节点</strong>
        <span>每个区间默认细分 4 倍</span>
      </div>
      <div class="step">
        <span class="step-label">03 · 守恒输移与反应</span>
        <strong>浓度 · 扩散 · 耗氧</strong>
        <span>面通量输移、反应耦合与质量核算</span>
      </div>
    </div>
    <p class="note">上图以 100 个水动力断面为例。支持 1、2、4、8 倍细分，节点数为（断面数 − 1）× 细分数 + 1；8 倍时为 793 个节点。</p>

    </QuoteText>

    <div class="benchmarks">
      <div class="benchmark-heading">
        <h3>独立解析基准验证</h3>
        <span class="badge">离线已验证 · 2026-10-20</span>
      </div>
      <p class="note">三组固定 100 个水动力断面的受控算例；数值越小，模拟浓度分布与独立解析参照越接近。</p>
      <div class="table-scroll" tabindex="0" role="region" aria-label="三组河段浓度分布误差对照表">
        <table>
          <caption>浓度分布误差（体积加权相对 L1）</caption>
          <thead>
            <tr>
              <th scope="col">河段长度</th>
              <th scope="col">原网格 · 100 节点</th>
              <th scope="col">默认 4 倍 · 397 节点</th>
              <th scope="col">8 倍 · 793 节点</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in benchmarks" :key="row.river">
              <th scope="row">{{ row.river }}</th>
              <td>{{ row.original }}</td>
              <td class="improved">{{ row.refined }}</td>
              <td>{{ row.fine }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="note">三组均通过质量积分与浓度非负检查。这些百分比是受控算例的数值误差，不能作为现场检测准确率。</p>
    </div>

    <details class="conditions">
      <summary>验证条件与适用范围</summary>
      <p>参照为有限时段点源的独立 Green 函数解析卷积；采用均匀恒定水力、已知单源、关闭氨氮反应，源位与网格对齐，边界在污染云团远场。细分时实际稳定性子步也随之改变，表格不表示单独的空间收敛阶数。</p>
      <p>当前模型支持一维恒定流情景；细分采用原水动力面积与流量插值，未增加实测断面信息。几何、弥散和反应参数仍需现场核实与率定，潮汐、支汊及二维回流暂不支持。</p>
    </details>

    <div class="actions">
      <button type="button" class="model-link" @click="$router.push('/river')">前往河道模型体验 →</button>
      <span class="note">在「污染事件模拟」中选择网格细分；更细网格会增加计算量，超限会明确提示。</span>
    </div>
  </div>
</template>

<style scoped>
.grid-tech { font-size: 14px; line-height: 1.85; color: var(--c-sub); }
.intro { margin: 0 0 15px; max-width: 820px; }
.intro b { color: var(--c-ink); }
.workflow { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.step { display: flex; flex-direction: column; gap: 5px; padding: 15px; border: 1px solid var(--c-border); border-radius: 9px; background: var(--c-surface-2); }
.step-label { font-size: 14px; }
.step strong { color: var(--c-ink); font-size: 18px; font-weight: 650; }
.step span:last-child { font-size: 14px; }
.step.featured { border-color: var(--c-accent); }
.step.featured strong { color: var(--c-accent); }
.note { margin: 9px 0; font-size: 14px; }
.benchmarks { margin-top: 20px; }
.benchmark-heading { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; }
.benchmark-heading h3 { margin: 0; font-size: 15px; font-weight: 600; color: var(--c-ink); }
.badge { padding: 2px 8px; border: 1px solid var(--c-border); border-radius: 20px; font-size: 14px; white-space: nowrap; }
.table-scroll { overflow-x: auto; border: 1px solid var(--c-border); border-radius: 8px; }
table { width: 100%; min-width: 490px; border-collapse: collapse; text-align: left; font-variant-numeric: tabular-nums; }
caption { text-align: left; padding: 9px 12px; background: var(--c-surface-2); font-size: 14px; }
th, td { padding: 9px 12px; border-top: 1px solid var(--c-border); }
thead th { font-size: 14px; font-weight: 500; }
tbody th { font-weight: 500; color: var(--c-ink); }
.improved { font-weight: 650; color: var(--c-accent); }
.conditions { margin-top: 13px; font-size: 14px; }
.conditions summary { cursor: pointer; color: var(--c-ink); }
.conditions p { margin: 8px 0; }
.actions { display: flex; flex-wrap: wrap; align-items: center; gap: 5px 12px; margin-top: 14px; }
.model-link { background: var(--c-accent); color: #06121c; border: none; border-radius: 7px; padding: 8px 15px; font-size: 14px; font-weight: 600; cursor: pointer; }
.model-link:focus-visible, .table-scroll:focus-visible { outline: 2px solid var(--c-accent); outline-offset: 3px; }
@media (max-width: 680px) { .workflow { grid-template-columns: 1fr; } }
</style>
