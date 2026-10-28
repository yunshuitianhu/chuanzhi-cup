<script setup>
/**
 * 多渠道污染溯源 —— 对应 app.py tab4 的第 3 区。
 *
 * 整合高德 POI、排污许可证、国控站点三路真实数据源，加权融合去重。
 * 未给坐标时自动取黑点最密集的聚类中心（已排除 DBSCAN 噪声簇 -1）。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../api/client'
import { useSessionStore } from '../stores/session'
import { toastErr, toastOk } from '../composables/useToast'
import { traceRequest } from '../utils/traceRequest'

const store = useSessionStore()
const lat = ref(null)
const lon = ref(null)
const radius = ref(500)
const loading = ref(false)
const data = ref(null)
const requestState = computed(() => traceRequest(lat.value,lon.value,radius.value))
const canRun = computed(() => !requestState.value.error && (requestState.value.manual || store.status.has_data))
const error = ref('')
const busy = computed(() => loading.value || store.loading || store.chatRunning || store.switchingProject || store.connecting || !store.ready)
let requestVersion = 0, disposed = false
// 状态对象会在查询后刷新；只在实际项目、监测版本或条件变化时撤销响应。
watch([() => store.projectState.current?.id, () => store.workspaceRevision, () => store.status.analysis_revision,
  () => store.switchingProject, lat, lon, radius], () => {
  requestVersion++
  data.value = null
  error.value = ''
}, {flush:'sync'})
onBeforeUnmount(() => { disposed = true; requestVersion++ })

async function run() {
  if (disposed || busy.value) return
  const request = requestState.value
  if (request.error) { toastErr(request.error); return }
  if (!canRun.value) return
  const version = ++requestVersion
  loading.value = true
  data.value = null
  error.value = ''
  try {
    const result = await store._run(() => api.multiSource(request.payload))
    if (disposed || version !== requestVersion) return
    data.value = result
    toastOk(`查询完成：返回 ${(result['综合污染源列表'] || []).length} 条候选线索`)
  } catch (e) {
    if (!disposed && version === requestVersion) {
      error.value = `溯源失败：${e.message}`
      toastErr(error.value)
    }
  } finally {
    if (!disposed) loading.value = false
  }
}

const SRC_COLOR = {
  高德POI: '#2ea3f2', 排污许可证: '#00c9a7', 国控站点: '#a78bfa', 重点排污单位: '#f59e0b',
}

/**
 * 搜索中心的展示文本。
 *
 * 后端历史上对同一字段返回过两种形态：数组 [lat, lon] 与字符串
 * "lat, lon"（内核原样透传时是字符串）。直接对字符串取 [0] 会得到单个
 * 字符，再调 .toFixed() 就抛 TypeError、整块渲染失败。
 * 这里两种形态都兼容，避免前端因数据形态差异而崩。
 */
const centerText = computed(() => {
  const c = data.value?.['搜索中心']
  if (Array.isArray(c)) return `${Number(c[0]).toFixed(5)}, ${Number(c[1]).toFixed(5)}`
  if (typeof c === 'string') return c
  return data.value?.['搜索中心文本'] || '—'
})
</script>

<template>
  <div class="wrap">
    <div class="bar">
      <label>纬度<input v-model.number="lat" type="number" :disabled="busy" step="0.000001" placeholder="留空自动" /></label>
      <label>经度<input v-model.number="lon" type="number" :disabled="busy" step="0.000001" placeholder="留空自动" /></label>
      <label>半径 m<input v-model.number="radius" type="number" :disabled="busy" min="50" max="50000" step="50" /></label>
      <button :disabled="!canRun || busy" @click="run">
        {{ loading ? '溯源中…' : '运行多渠道溯源' }}
      </button>
    </div>
    <p v-if="error" class="note warn" role="alert">{{ error }}</p>
    <p v-if="requestState.error" class="tip note warn">{{ requestState.error }}</p>
    <p v-else class="tip">
      <template v-if="!store.status.has_black_spots">
        未检出黑点也可查询：留空使用走航轨迹代表采样点；或手动填写WGS-84坐标。结果仅作周边背景线索。
      </template>
      <template v-else>坐标留空时自动使用黑点最密集的聚类中心。</template>
    </p>

    <template v-if="data">
      <div class="meta">
        <span class="chip">搜索中心 {{ centerText }}</span>
        <span class="chip">半径 {{ data['搜索半径_m'] }} m</span>
        <span v-for="(n, src) in data['各数据源贡献']" :key="src" class="chip">
          <i :style="{ background: SRC_COLOR[src] || '#56687a' }"></i>{{ src }} {{ n }}
        </span>
      </div>

      <p class="note warn">{{ data['坐标系说明'] }}</p>
      <p class="note">搜索中心来源：{{ data['搜索中心来源'] || '当前查询坐标' }}</p>
      <p class="note warn">{{ data['查询用途'] }}</p>
      <p v-if="data['状态']" class="note">{{ data['状态'] }} —— {{ data['建议'] }}</p>

      <div v-if="(data['综合污染源列表'] || []).length" class="table-wrap">
        <table>
          <thead>
            <tr><th>#</th><th>名称</th><th>数据源</th><th>权重</th><th>距离</th><th>地址</th></tr>
          </thead>
          <tbody>
            <tr v-for="s in data['综合污染源列表']" :key="s['排名']">
              <td>{{ s['排名'] }}</td>
              <td class="name">{{ s['名称'] || '—' }}</td>
              <td>
                <span class="src" :style="{ borderColor: SRC_COLOR[s['数据源']] || '#56687a' }">
                  {{ s['数据源'] || '—' }}
                </span>
              </td>
              <td>{{ s['置信度权重'] ?? '—' }}</td>
              <td>{{ s['距离'] || s['distance'] || '—' }}</td>
              <td class="addr">{{ s['地址'] || s['address'] || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <details v-if="data['数据源说明']" class="srcs">
        <summary>数据来源说明</summary>
        <div v-for="(v, k) in data['数据源说明']" :key="k" class="src-row">
          <b>{{ k }}</b>{{ v }}
        </div>
      </details>
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 10px; flex-wrap: wrap; align-items: flex-end; margin-bottom: 8px; }
label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--c-sub); }
input {
  background: var(--c-surface-2); border: 1px solid var(--c-border); color: var(--c-ink);
  border-radius: 7px; padding: 6px 10px; font-family: inherit; font-size: 12px; width: 130px;
}
button {
  background: var(--c-accent); color: #06121c; border: none;
  padding: 8px 18px; border-radius: 7px; font-weight: 600; font-size: 13px;
}
button:disabled { opacity: .45; cursor: not-allowed; }

.tip { margin: 0 0 12px; font-size: 12px; color: var(--c-sub); }
.meta { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
.chip {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 12px; color: var(--c-sub); background: var(--c-surface-2);
  border: 1px solid var(--c-border); padding: 3px 10px; border-radius: 20px;
}
.chip i { width: 7px; height: 7px; border-radius: 50%; }
.note { font-size: 12px; color: var(--c-sub); margin: 4px 0; }
.note.warn { color: #e8c98a; }

.table-wrap { max-height: 380px; overflow: auto; border: 1px solid var(--c-border); border-radius: 8px; margin-top: 8px; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th {
  position: sticky; top: 0; background: var(--c-surface-2); padding: 8px 10px;
  text-align: left; font-weight: 600; color: var(--c-sub);
  border-bottom: 1px solid var(--c-border); white-space: nowrap;
}
td { padding: 6px 10px; border-bottom: 1px solid #1a242e; }
tr:hover td { background: #1a2530; }
.name { font-weight: 600; }
.addr { color: var(--c-sub); font-size: 12px; max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.src { border: 1px solid; border-radius: 20px; padding: 1px 8px; font-size: 12px; }

.srcs { margin-top: 12px; font-size: 12px; color: var(--c-sub); }
.srcs summary { cursor: pointer; padding: 4px 0; }
.src-row { margin: 4px 0; line-height: 1.7; }
.src-row b { color: var(--c-ink); margin-right: 6px; }
</style>
