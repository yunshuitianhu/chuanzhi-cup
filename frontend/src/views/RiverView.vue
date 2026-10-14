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
import { computed, onMounted, ref } from 'vue'
import { api } from '../api/client'
import { useSessionStore } from '../stores/session'
import { toastErr, toastOk } from '../composables/useToast'
import Panel from '../components/Panel.vue'
import EChart from '../components/EChart.vue'

const store = useSessionStore()

const params = ref(null)
const initResult = ref(null)
const hydroResult = ref(null)
const pollutionResult = ref(null)
const busy = ref('')

const form = ref({
  n_cross_sections: 50,
  mannings_n: 0.028,
  upstream_flow_m3s: 6.9,
  tidal_amplitude_m: 0,     // 默认恒定流；潮汐工况有已知缺陷
  duration_h: 6,
  load_kg: 50,
  chainage_m: 1600,
  pollutant_type: 'ammonia',
})

onMounted(detectParams)

async function detectParams() {
  if (!store.status.has_data) return
  busy.value = 'params'
  try {
    params.value = await api.riverParams()
    form.value.upstream_flow_m3s = params.value.avg_discharge_m3s || 6.9
    form.value.mannings_n = params.value.mannings_n || 0.028
    toastOk(`识别到河道：${params.value.river_name}`)
  } catch (e) {
    toastErr(`参数检测失败：${e.message}`)
  } finally {
    busy.value = ''
  }
}

async function runInit() {
  busy.value = 'init'
  try {
    initResult.value = await api.riverInit({
      n_cross_sections: form.value.n_cross_sections,
      mannings_n: form.value.mannings_n,
      auto_detect: true,
    })
    toastOk('模型初始化完成')
  } catch (e) {
    toastErr(`初始化失败：${e.message}`)
  } finally {
    busy.value = ''
  }
}

async function runHydro() {
  busy.value = 'hydro'
  try {
    hydroResult.value = await api.riverHydro({
      upstream_flow_m3s: form.value.upstream_flow_m3s,
      tidal_amplitude_m: form.value.tidal_amplitude_m,
      duration_h: form.value.duration_h,
    })
    toastOk('水动力模拟完成')
  } catch (e) {
    toastErr(`水动力模拟失败：${e.message}`)
  } finally {
    busy.value = ''
  }
}

async function runPollution() {
  busy.value = 'pollution'
  try {
    pollutionResult.value = await api.riverPollution({
      chainage_m: form.value.chainage_m,
      pollutant_type: form.value.pollutant_type,
      load_kg: form.value.load_kg,
      simulation_hours: form.value.duration_h,
    })
    toastOk('污染事件模拟完成')
  } catch (e) {
    toastErr(`污染模拟失败：${e.message}`)
  } finally {
    busy.value = ''
  }
}

const CONF_LABEL = {
  known_river_database: ['🟢 已知河道库', 'good'],
  gps: ['🟢 GPS 轨迹', 'good'],
  osm: ['🟡 OSM', 'mid'],
  regional_defaults: ['🟡 区域默认值', 'mid'],
  estimated_from_width: ['🟡 经验公式', 'mid'],
  estimated: ['🟠 经验估算', 'low'],
  amap_address: ['🟠 高德地址', 'low'],
  amap_reverse_geocode: ['🟡 高德逆地理', 'mid'],
  default: ['🔴 默认值', 'bad'],
  unknown: ['🔴 未知', 'bad'],
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
               formatter: '{c} 个断面' },
    }],
  }
})
</script>

<template>
  <Panel title="水动力-水质模型" subtitle="一维 Saint-Venant 方程 + ADR 对流扩散反应模型">
    <!-- 局限说明：固定展示，不可关闭 -->
    <div class="caveat">
      <div class="cv-title">⚠️ 已知局限（请务必阅读）</div>
      <p>
        本模型的<b>非恒定（潮汐）工况存在已确认的数值缺陷</b>：连续性方程遗漏水面宽
        <code>B_mid</code>，导致质量不守恒（实测残差达蓄水率的 103%）。
      </p>
      <p>
        · <b>恒定流工况</b>（潮汐振幅 = 0）的结果可用 —— 质量守恒已验证成立<br />
        · <b>潮汐工况</b>的结果不可作为结论依据
      </p>
      <p class="cv-foot">
        详见 <code>docs/代码审计与修改方案.md</code> 第 2.8 节。演示与文档中不应把「潮汐模拟」作为宣传点。
      </p>
    </div>

    <div v-if="!store.status.has_data" class="empty">请先在左侧上传并加载走航数据。</div>

    <template v-else>
      <!-- 参数自动检测 -->
      <section class="block">
        <h3 class="st">① 河道参数自动检测</h3>
        <p class="desc">
          6 层检测管道：已知河道坐标库 → OSM → 高德逆地理 → 卫星影像 → 区域默认值 → 监测数据推断。
          坐标落在已知河道 bbox 内时直接命中标定参数（置信度 0.95），无需联网。
        </p>
        <button class="mini" :disabled="busy === 'params'" @click="detectParams">
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
        <div class="grid">
          <label>断面数<input v-model.number="form.n_cross_sections" type="number" min="20" max="100" /></label>
          <label>曼宁糙率<input v-model.number="form.mannings_n" type="number" step="0.001" /></label>
          <label>上游流量 m³/s<input v-model.number="form.upstream_flow_m3s" type="number" step="0.1" /></label>
          <label>
            潮汐振幅 m
            <input v-model.number="form.tidal_amplitude_m" type="number" step="0.1" min="0" />
            <small v-if="form.tidal_amplitude_m > 0" class="warn-text">非零即进入已知缺陷工况</small>
          </label>
          <label>模拟时长 h<input v-model.number="form.duration_h" type="number" min="1" max="24" /></label>
        </div>

        <div class="btn-row">
          <button class="mini" :disabled="busy === 'init'" @click="runInit">
            {{ busy === 'init' ? '初始化中…' : '初始化模型' }}
          </button>
          <button
            class="mini primary"
            :disabled="busy === 'hydro' || !initResult"
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
              <option value="cod">COD</option>
            </select>
          </label>
          <label>排放量 kg<input v-model.number="form.load_kg" type="number" step="10" /></label>
          <label>污染源桩号 m<input v-model.number="form.chainage_m" type="number" step="100" /></label>
        </div>
        <button
          class="mini primary"
          :disabled="busy === 'pollution' || !hydroResult"
          :title="hydroResult ? '' : '请先运行水动力模拟'"
          @click="runPollution"
        >
          {{ busy === 'pollution' ? '模拟中…' : '模拟污染事件' }}
        </button>
        <p v-if="!hydroResult" class="seq-hint">「模拟污染事件」需先运行水动力 —— 它提供流速场。</p>

        <template v-if="pollutionResult">
          <div class="result">
            <span>峰值浓度 <b>{{ pollutionResult['峰值浓度_mgL'] }} mg/L</b></span>
            <span>峰值时刻 <b>{{ pollutionResult['峰值时刻_h'] }} h</b></span>
            <span>超标倍数 <b>{{ pollutionResult['超标倍数'] }}</b></span>
          </div>
          <p class="cred">{{ pollutionResult['提示'] }}</p>
          <EChart v-if="pollutionCompare" :option="pollutionCompare" :height="150" />
          <p class="hint">
            两个口径的基准不同，故结论未必一致：左为「是否超 GB3838 限值」，
            右为「本次排放相对监测基线的增量影响」。
          </p>
        </template>
      </section>
    </template>
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
