<script setup>
/**
 * 河道模型页 —— 对应 app.py 的 tab3。
 *
 * ⚠️ 本页顶部固定展示模型的已知局限。这不是可选的装饰：
 * 非恒定（潮汐）工况存在已确认的数值缺陷（连续性方程遗漏水面宽，
 * 质量不守恒，实测残差达蓄水率 103%），恒定流工况则已验证成立。
 * 演示与文档中不得把"潮汐模拟"作为宣传点。
 * 参数默认取 `tidal_amplitude_m = 0`（恒定流），要跑潮汐需用户显式开启。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../api/client'
import { useSessionStore } from '../stores/session'
import { toastErr, toastOk } from '../composables/useToast'
import Panel from '../components/Panel.vue'
import EChart from '../components/EChart.vue'
import PollutionPlayback from '../components/PollutionPlayback.vue'
import NumericalResolution from '../components/NumericalResolution.vue'
import CruiseAlignment from '../components/CruiseAlignment.vue'
import { restoreRiverWorkspace } from '../utils/projectRestore'

const store = useSessionStore()

const params = ref(null)
const initResult = ref(null)
const hydroResult = ref(null)
const pollutionResult = ref(null)
const busy = ref('')
const alignmentRevision = ref(0)
const playbackRevision = computed(()=>({pollution:pollutionResult.value,alignment:alignmentRevision.value}))

const form = ref({
  n_cross_sections: 50,
  mannings_n: 0.03,
  river_name: '自定义河段', length_m: 1000, width_avg_m: 30, depth_avg_m: 2,
  longitudinal_slope: .0001, bed_elevation_avg_m: 0, high_water_mean_m: 2,
  ammonia_nh3_mgL: 3, dissolved_oxygen_mgL: 2,
  bod_cbod_mgL: 20, water_temp_C: 25, k_nitrification: .15, k_cbod_decay: .25,
  k_sediment_oxygen_demand: 1, do_saturation_mgL: 8.25,
  upstream_flow_m3s: 6,
  tidal_amplitude_m: 0,     // 默认恒定流；潮汐工况有已知缺陷
  duration_h: 6,
  load_kg: 50,
  source_start_h: 0,
  duration_min: 30,
  chainage_m: 500,
  pollutant_type: 'ammonia',
  wq_refinement: 4,
})

let hydrating = false
let disposed = false
onBeforeUnmount(() => { disposed = true })
onMounted(async () => {
  const workspaceRevision = store.workspaceRevision
  hydrating = true
  try {
    const saved = await api.projects()
    if (disposed || workspaceRevision !== store.workspaceRevision || store.switchingProject) return
    store.projectState = saved
    const restored = restoreRiverWorkspace(store.projectState,store.riverForm,form.value)
    Object.assign(form.value,restored.form)
    await nextTick()
    if (disposed) return
    initResult.value = restored.init
    hydroResult.value = restored.hydro
    pollutionResult.value = restored.pollution
    store.riverForm = { ...form.value }
    // 已配置项目优先恢复用户参数，避免自动建议覆盖另一条河流的设置。
    if (!store.projectState.model?.initialized && !Object.keys(store.projectState.river_ui?.form || {}).length && store.status.has_data) await detectParams()
  } catch (e) { toastErr(`项目参数恢复失败：${e.message}`) }
  finally { hydrating = false }
})
watch(form, () => { store.riverForm = { ...form.value } }, { deep:true })
watch(() => store.status.analysis_revision, () => {
  params.value = null
  initResult.value = null
  hydroResult.value = null
  pollutionResult.value = null
  busy.value = ''
  store.riverForm = null
  detectParams()
})
watch(() => ['river_name','length_m','width_avg_m','depth_avg_m','longitudinal_slope',
  'bed_elevation_avg_m','mannings_n','n_cross_sections','ammonia_nh3_mgL','dissolved_oxygen_mgL',
  'bod_cbod_mgL','water_temp_C','k_nitrification','k_cbod_decay','k_sediment_oxygen_demand','do_saturation_mgL']
  .map(k => form.value[k]), () => {
    if (hydrating) return
    initResult.value = hydroResult.value = pollutionResult.value = null
})
watch(() => [form.value.upstream_flow_m3s, form.value.high_water_mean_m, form.value.duration_h], () => {
  if (hydrating) return
  hydroResult.value = pollutionResult.value = null
})
watch(() => [form.value.wq_refinement, form.value.chainage_m, form.value.pollutant_type,
  form.value.load_kg, form.value.source_start_h, form.value.duration_min], () => {
  if (!hydrating) pollutionResult.value = null
})

async function detectParams() {
  if (!store.status.has_data) return
  const formSnapshot = JSON.stringify(form.value)
  const revision = store.status.analysis_revision
  busy.value = 'params'
  try {
    const result = await api.riverParams()
    if (revision !== store.status.analysis_revision) return
    params.value = result
    if (JSON.stringify(form.value) !== formSnapshot) {
      toastOk('参数建议已返回，已保留你正在编辑的手工参数')
      return
    }
    for (const key of ['river_name', 'length_m', 'width_avg_m', 'depth_avg_m', 'longitudinal_slope',
      'bed_elevation_avg_m', 'high_water_mean_m', 'ammonia_nh3_mgL', 'dissolved_oxygen_mgL', 'mannings_n']) {
      if (params.value[key] != null) form.value[key] = params.value[key]
    }
    form.value.upstream_flow_m3s = params.value.avg_discharge_m3s ?? 6
    form.value.chainage_m = form.value.length_m / 2
    toastOk(`识别到河道：${params.value.river_name}`)
  } catch (e) {
    toastErr(`参数检测失败：${e.message}`)
  } finally {
    if (revision === store.status.analysis_revision) busy.value = ''
  }
}

async function runInit() {
  const revision = store.status.analysis_revision
  initResult.value = hydroResult.value = pollutionResult.value = null
  busy.value = 'init'
  try {
    const result = await api.riverInit({
      n_cross_sections: form.value.n_cross_sections,
      auto_detect: false,
      parameters: Object.fromEntries(['river_name', 'length_m', 'width_avg_m', 'depth_avg_m',
        'longitudinal_slope', 'bed_elevation_avg_m', 'high_water_mean_m', 'ammonia_nh3_mgL',
        'dissolved_oxygen_mgL', 'mannings_n', 'bod_cbod_mgL','water_temp_C','k_nitrification',
        'k_cbod_decay','k_sediment_oxygen_demand','do_saturation_mgL'].map(k => [k, form.value[k]]).concat([
        ['avg_discharge_m3s', form.value.upstream_flow_m3s]])),
    })
    if (revision !== store.status.analysis_revision) return
    initResult.value = result
    toastOk('模型初始化完成')
  } catch (e) {
    toastErr(`初始化失败：${e.message}`)
  } finally {
    if (revision === store.status.analysis_revision) busy.value = ''
  }
}

async function runHydro() {
  const revision = store.status.analysis_revision
  hydroResult.value = pollutionResult.value = null
  busy.value = 'hydro'
  try {
    const result = await api.riverHydro({
      upstream_flow_m3s: form.value.upstream_flow_m3s,
      downstream_stage_m: form.value.high_water_mean_m,
      tidal_amplitude_m: form.value.tidal_amplitude_m,
      duration_h: form.value.duration_h,
    })
    if (revision !== store.status.analysis_revision) return
    hydroResult.value = result
    toastOk('水动力模拟完成')
  } catch (e) {
    toastErr(`水动力模拟失败：${e.message}`)
  } finally {
    if (revision === store.status.analysis_revision) busy.value = ''
  }
}

async function runPollution() {
  const revision = store.status.analysis_revision
  pollutionResult.value = null
  busy.value = 'pollution'
  try {
    const result = await api.riverPollution({
      chainage_m: form.value.chainage_m,
      pollutant_type: form.value.pollutant_type,
      load_kg: form.value.load_kg,
      source_start_h: form.value.source_start_h,
      duration_min: form.value.duration_min,
      simulation_hours: form.value.duration_h,
      wq_refinement: form.value.wq_refinement,
    })
    if (revision !== store.status.analysis_revision) return
    pollutionResult.value = result
    toastOk('污染事件模拟完成')
  } catch (e) {
    toastErr(`污染模拟失败：${e.message}`)
  } finally {
    if (revision === store.status.analysis_revision) busy.value = ''
  }
}

const CONF_LABEL = {
  known_river_database: ['🟢 已知河道库', 'good'],
  gps: ['🟢 GPS 轨迹', 'good'],
  gps_track_unregistered: ['航程建议·待参考线核实', 'low'],
  osm: ['🟡 OSM', 'mid'],
  regional_defaults: ['🟡 区域默认值', 'mid'],
  estimated_from_width: ['🟡 经验公式', 'mid'],
  estimated: ['🟠 经验估算', 'low'],
  amap_address: ['🟠 高德地址', 'low'],
  amap_reverse_geocode: ['🟡 高德逆地理', 'mid'],
  default: ['🔴 默认值', 'bad'],
  unknown: ['🔴 未知', 'bad'],
  user_scenario: ['🟠 用户情景输入', 'low'],
  manning_geometry_estimate: ['🟠 几何与曼宁公式估计', 'low'],
}

const paramRows = computed(() => {
  if (!params.value) return []
  const p = params.value
  return [
    ['河道名称', p.river_name, 'river_name'],
    ['位置', p.location, 'location'],
    ['河段长度', `${p.length_m} m`, 'length_m'],
    ['平均宽度', `${p.width_avg_m} m`, 'width_avg_m'],
    ['平均水深', `${p.depth_avg_m} m`, 'depth_avg_m'],
    ['曼宁糙率', p.mannings_n, 'mannings_n'],
    ['纵向坡降', p.longitudinal_slope, 'longitudinal_slope'],
    ['平均流量', `${p.avg_discharge_m3s} m³/s`, 'avg_discharge_m3s'],
    ['潮差', `${p.tidal_range_m} m`, 'tidal_range_m'],
  ]
})

/** 污染事件的两个口径画成条形对比 —— 它们基准不同，并排展示才不会被误读。 */
const pollutionCompare = computed(() => {
  const d = pollutionResult.value
  if (!d) return null
  const std = d['相对标准限值'] || {}
  const base = d['相对监测基线'] || {}
  return {
    grid: { left: 96, right: 60, top: 20, bottom: 28 },
    tooltip: {
      backgroundColor: 'rgba(20,30,40,.95)', borderColor: '#2a3947',
      textStyle: { color: '#e8eef5', fontSize: 12 },
    },
    xAxis: {
      type: 'value', axisLabel: { color: '#94a7b8' },
      splitLine: { lineStyle: { color: '#1e2a36' } },
    },
    yAxis: {
      type: 'category',
      data: [`相对标准限值 (${std['基准_mgL']} mg/L)`, `相对监测基线 (${base['基准_mgL']} mg/L)`],
      axisLabel: { color: '#94a7b8', fontSize: 11 },
      axisLine: { lineStyle: { color: '#2a3947' } },
    },
    series: [{
      type: 'bar', barWidth: '42%',
      data: [
        { value: std['断面数'] ?? 0, itemStyle: { color: '#ff4d6d' } },
        { value: base['断面数'] ?? 0, itemStyle: { color: '#2ea3f2' } },
      ],
      label: { show: true, position: 'right', color: '#e8eef5', fontSize: 11,
               formatter: '{c} 个水质节点' },
    }],
  }
})
</script>

<template>
  <Panel title="水动力-水质模型" subtitle="一维简化水动力 + 守恒输移与耦合反应（恒定流情景）">
    <!-- 局限说明：固定展示，不可关闭 -->
    <div class="caveat">
      <div class="cv-title">模型适用范围</div>
      <p>项目自带求解器，无需安装商业水动力软件。支持自定义河段的一维恒定流情景分析。</p>
      <p>几何、边界和反应参数仍需现场核实与率定；潮汐、支汊及二维回流暂不支持，不能据此认定污染责任。</p>
    </div>

    <div v-if="!store.status.has_data" class="empty">未加载走航数据时可手工配置并运行情景模型；自动参数建议和轨迹动画需上传对应数据。</div>

    <div class="river-workspace">
      <!-- 参数自动检测 -->
      <section class="block">
        <h3 class="st">① 河道参数自动检测</h3>
        <p class="desc">
          GPS 与地名识别提供位置和长度建议；缺失的水力参数以经验关系生成初始情景，需手工核实。
          参数建议不等于实测断面。缺少 GPS 时请直接手工配置；不自动套用任何特定河流的几何或流量。
        </p>
        <button class="mini" :disabled="!!busy || store.chatRunning || !store.status.has_data" @click="detectParams">
          {{ busy === 'params' ? '检测中…' : '重新检测' }}
        </button>

        <div v-if="params" class="params">
          <div v-for="[label, val, key] in paramRows" :key="key" class="prow">
            <span class="pk">{{ label }}</span>
            <span class="pv">{{ val }}</span>
            <span class="ps" :class="(CONF_LABEL[params.param_sources[key]] || ['',''])[1]">
              {{ (CONF_LABEL[params.param_sources[key]] || [params.param_sources[key] || '—'])[0] }}
              <i v-if="params.param_confidence[key] != null">
                {{ (params.param_confidence[key] * 100).toFixed(0) }}%
              </i>
            </span>
          </div>
        </div>
      </section>

      <!-- 模拟参数与运行 -->
      <section class="block">
        <h3 class="st">② 运行模拟</h3>
        <p class="desc">以下参数均可编辑。初始模板仅用于情景分析；水位与河底须使用同一高程基准。修改后重新初始化，才会进入本次计算。</p>
        <div class="grid">
          <label>河段名称<input v-model="form.river_name" /></label>
          <label>河段长度 m<input v-model.number="form.length_m" type="number" min="10" /></label>
          <label>参考水面宽 m<input v-model.number="form.width_avg_m" type="number" min="0.5" /></label>
          <label>参考水深 m<input v-model.number="form.depth_avg_m" type="number" min="0.1" step="0.1" /></label>
          <label>河底坡降<input v-model.number="form.longitudinal_slope" type="number" min="0" step="0.0001" /></label>
          <label>平均河底高程 m<input v-model.number="form.bed_elevation_avg_m" type="number" step="0.1" /></label>
          <label>下游水位 m<input v-model.number="form.high_water_mean_m" type="number" step="0.1" /></label>
          <label>初始氨氮 mg/L<input v-model.number="form.ammonia_nh3_mgL" type="number" min="0" step="0.1" /></label>
          <label>初始 DO mg/L<input v-model.number="form.dissolved_oxygen_mgL" type="number" min="0" step="0.1" /></label>
          <label>断面数<input v-model.number="form.n_cross_sections" type="number" min="3" max="100" /></label>
          <label>曼宁糙率<input v-model.number="form.mannings_n" type="number" step="0.001" /></label>
          <label>上游流量 m³/s<input v-model.number="form.upstream_flow_m3s" type="number" step="0.1" /></label>
          <label>模拟时长 h<input v-model.number="form.duration_h" type="number" min="0.5" max="48" step="0.5" /></label>
        </div>

        <details class="hint">
          <summary>反应情景参数（未经现场率定，可按河段修改）</summary>
          <p>CBOD 不由 COD 直接换算；饱和氧需结合温度、盐度和气压核实。修改后重新初始化。</p>
          <div class="grid">
            <label>初始 CBOD mg/L<input v-model.number="form.bod_cbod_mgL" type="number" min="0" /></label>
            <label>水温 °C<input v-model.number="form.water_temp_C" type="number" min="0" max="45" /></label>
            <label>硝化速率 d⁻¹<input v-model.number="form.k_nitrification" type="number" min="0" step="0.01" /></label>
            <label>CBOD 衰减 d⁻¹<input v-model.number="form.k_cbod_decay" type="number" min="0" step="0.01" /></label>
            <label>底泥耗氧 g/m²/d<input v-model.number="form.k_sediment_oxygen_demand" type="number" min="0" step="0.1" /></label>
            <label>饱和 DO mg/L<input v-model.number="form.do_saturation_mgL" type="number" min="0.1" step="0.1" /></label>
          </div>
        </details>
        <div class="btn-row">
          <button class="mini" :disabled="!!busy || store.chatRunning" @click="runInit">
            {{ busy === 'init' ? '初始化中…' : '初始化模型' }}
          </button>
          <button
            class="mini primary"
            :disabled="!!busy || store.chatRunning || !initResult"
            :title="initResult ? '' : '请先点左侧「初始化模型」'"
            @click="runHydro"
          >
            {{ busy === 'hydro' ? '计算中…' : '运行水动力' }}
          </button>
        </div>
        <!-- 按顺序禁用的按钮必须说明原因，否则就是一个看不出所以然的死按钮 -->
        <p v-if="!initResult" class="seq-hint">「运行水动力」需先点「初始化模型」生成断面序列。</p>

        <div v-if="initResult" class="result">
          <span>断面数 <b>{{ initResult['断面数'] }}</b></span>
          <span>河段长度 <b>{{ initResult['河段长度_km'] }} km</b></span>
          <span>糙率 <b>{{ initResult['曼宁糙率'] }}</b></span>
        </div>

        <div v-if="hydroResult" class="result">
          <span>工况 <b>{{ hydroResult['工况'] }}</b></span>
          <span>帧数 <b>{{ hydroResult['输出帧数'] }}</b></span>
          <span>水位变幅 <b>{{ hydroResult['水位变幅_m'] }} m</b></span>
          <span>最大流速 <b>{{ hydroResult['最大流速_ms'] }} m/s</b></span>
        </div>
        <p v-if="hydroResult" class="cred">{{ hydroResult['结果可信度'] }}</p>
      </section>

      <!-- 污染事件 -->
      <section class="block">
        <h3 class="st">③ 污染事件模拟（ADR 模型）</h3>
        <div class="grid">
          <label>污染物
            <select v-model="form.pollutant_type">
              <option value="ammonia">氨氮</option>
              <option value="cbod">CBOD</option>
            </select>
          </label>
          <label>排放量 kg<input v-model.number="form.load_kg" type="number" step="10" /></label>
          <label>污染源桩号 m<input v-model.number="form.chainage_m" type="number" step="100" /></label>
          <label>排放开始 h<input v-model.number="form.source_start_h" type="number" min="0" step="0.1" /></label>
          <label>排放持续 min<input v-model.number="form.duration_min" type="number" min="0.1" step="1" /></label>
          <label>水质网格细分
            <select v-model.number="form.wq_refinement">
              <option :value="1">1 倍（原网格，对照）</option>
              <option :value="2">2 倍</option>
              <option :value="4">4 倍（默认）</option>
              <option :value="8">8 倍（精细复算）</option>
            </select>
          </label>
        </div>
        <p class="hint">水质网格可独立细分，无需重新运行水动力。预计 {{ (form.n_cross_sections - 1) * form.wq_refinement + 1 }} 个水质节点；更细网格会增加计算量。几何信息仍来自原水动力断面。</p>
        <button
          class="mini primary"
          :disabled="!!busy || store.chatRunning || !hydroResult"
          :title="hydroResult ? '' : '请先运行水动力模拟'"
          @click="runPollution"
        >
          {{ busy === 'pollution' ? '模拟中…' : '模拟污染事件' }}
        </button>
        <p v-if="!hydroResult" class="seq-hint">「模拟污染事件」需先运行水动力 —— 它提供流速场。</p>
        <p class="hint">COD 尚未实现独立组分模拟，当前支持氨氮和 CBOD，不使用 CBOD 冒充 COD。</p>

        <template v-if="pollutionResult">
          <div class="result">
            <span>峰值浓度 <b>{{ pollutionResult['峰值浓度_mgL'] }} mg/L</b></span>
            <span>峰值时刻 <b>{{ pollutionResult['峰值时刻_h'] }} h</b></span>
            <span>超标倍数 <b>{{ pollutionResult['超标倍数'] }}</b></span>
            <span>本时段实际注入 <b>{{ pollutionResult['实际注入量_kg']?.toFixed(3) ?? '—' }} kg</b></span>
            <span>输出时间点 <b>{{ pollutionResult['输出时间点数'] }}</b></span>
          </div>
          <p class="hint">排放开始时间相对模拟起点计算；本时段仅计入与排放时段重叠的负荷。源位置取最近水质计算节点，实际计算桩号 {{ pollutionResult['源位置离散']?.['实际计算桩号_m'] ?? '—' }} m，偏移 {{ pollutionResult['源位置离散']?.['与请求位置距离_m'] ?? '—' }} m。</p>
          <p class="cred">{{ pollutionResult['提示'] }}</p>
          <NumericalResolution :diagnostics="pollutionResult['数值诊断']" />
          <div v-if="pollutionResult['反应模型']?.version" class="reaction-info">
            <p>本次计算包含硝化耗氧、低氧限制及硝态氮转化。</p>
            <div class="result">
              <span>末时刻最低 DO <b>{{ pollutionResult['反应核算']?.final_minimum_do_mgL?.toFixed(3) ?? '—' }} mg/L</b></span>
              <span>硝化耗氧 <b>{{ pollutionResult['反应核算']?.nitrification_oxygen_demand_kg?.toFixed(3) ?? '—' }} kg O₂</b></span>
            </div>
            <p class="hint">{{ pollutionResult['反应模型'].parameter_status }}。{{ pollutionResult['反应模型'].simplifications }}。</p>
          </div>
          <EChart v-if="pollutionCompare" :option="pollutionCompare" :height="150" />
          <h3 class="st">④ 污染扩散演示</h3>
          <PollutionPlayback :revision="playbackRevision" />
          <p class="hint">
            两个口径的基准不同，故结论未必一致：左为「是否超 GB3838 限值」，
            右为「本次排放相对监测基线的增量影响」。
          </p>
        </template>
      </section>
    </div>
    <CruiseAlignment :revision="pollutionResult" :model-busy="!!busy" @updated="alignmentRevision++" />
  </Panel>
</template>

<style scoped>
.caveat {
  background: #2a1e10; border: 1px solid #6b4a10; border-radius: 9px;
  padding: 13px 17px; margin-bottom: 18px; font-size: 12px; line-height: 1.85;
  color: #e8c98a;
}
.cv-title { font-weight: 700; margin-bottom: 5px; font-size: 13px; }
.caveat p { margin: 5px 0; }
.caveat code { background: #3a2a14; padding: 1px 5px; border-radius: 3px; }
.cv-foot { opacity: .8; font-size: 11px; }

.empty { padding: 40px; text-align: center; color: var(--c-sub); }

.block { padding: 18px 0; border-bottom: 1px solid var(--c-border); }
.block:last-child { border-bottom: none; }
.st { margin: 0 0 8px; font-size: 14px; }
.desc { margin: 0 0 12px; font-size: 12px; color: var(--c-sub); line-height: 1.85; max-width: 800px; }

.mini {
  background: transparent; border: 1px solid var(--c-border); color: var(--c-ink);
  padding: 7px 16px; border-radius: 7px; font-size: 12px;
}
.mini:hover:not(:disabled) { border-color: var(--c-accent); }
.mini:disabled { opacity: .45; cursor: not-allowed; }
.mini.primary { background: var(--c-accent); color: #06121c; border-color: var(--c-accent); font-weight: 600; }

.params { margin-top: 14px; display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 6px; }
.prow {
  display: flex; align-items: baseline; gap: 8px; font-size: 12px;
  background: var(--c-surface-2); border-radius: 6px; padding: 7px 11px;
}
.pk { color: var(--c-sub); min-width: 74px; }
.pv { flex: 1; font-weight: 600; }
.ps { font-size: 11px; color: var(--c-sub); }
.ps i { opacity: .75; font-style: normal; margin-left: 3px; }
.ps.good { color: var(--c-accent-2); }
.ps.mid { color: var(--sev-mild); }
.ps.low { color: #f59e0b; }
.ps.bad { color: #ff6b81; }

.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 10px; margin-bottom: 12px; }
.grid label { display: flex; flex-direction: column; gap: 5px; font-size: 11px; color: var(--c-sub); }
.grid input, .grid select {
  background: var(--c-surface-2); border: 1px solid var(--c-border); color: var(--c-ink);
  border-radius: 7px; padding: 7px 10px; font-family: inherit; font-size: 13px;
}
.warn-text { color: var(--sev-mild); font-size: 10px; }

.btn-row { display: flex; gap: 10px; }

.result {
  display: flex; gap: 18px; flex-wrap: wrap; margin-top: 12px;
  font-size: 12px; color: var(--c-sub);
}
.result b { color: var(--c-ink); font-size: 14px; margin-left: 3px; }
.cred { margin: 8px 0 0; font-size: 11px; color: #e8c98a; line-height: 1.7; }
.hint { margin: 6px 0 0; font-size: 11px; color: var(--c-sub); }
.seq-hint {
  margin: 8px 0 0; font-size: 11px; color: #e8c98a;
  padding: 6px 11px; background: #2a1e10; border-radius: 6px;
  border-left: 2px solid #6b4a10; display: inline-block;
}
</style>
