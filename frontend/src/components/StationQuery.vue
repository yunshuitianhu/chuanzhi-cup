<script setup>
/**
 * 国控水质站点实时查询 —— 对应 app.py tab4 的第 1 区。
 *
 * 数据来自生态环境部「国家地表水水质自动监测实时数据发布系统」，
 * 覆盖全国 1800+ 断面、每小时更新。实时模式必须选省份 ——
 * 全量拉取会让政府服务器超时（内核已对此做了保护）。
 */
import { onMounted, ref } from 'vue'
import { api } from '../api/client'
import { toastErr, toastOk } from '../composables/useToast'

const provinces = ref([])
const province = ref('广东省')
const keyword = ref('')
const live = ref(true)
const loading = ref(false)
const data = ref(null)

onMounted(async () => {
  try {
    provinces.value = (await api.provinces()).provinces
  } catch { /* 省份列表拉不到不影响手输 */ }
})

async function run() {
  loading.value = true
  try {
    data.value = await api.queryStations({
      province: province.value, keyword: keyword.value, live: live.value,
    })
    toastOk(`查询完成：${data.value['站点数量']} 个站点`)
  } catch (e) {
    toastErr(`查询失败：${e.message}`)
  } finally {
    loading.value = false
  }
}

/** 实时模式返回的列 与 离线模式返回的列 不同，分开定义。 */
const LIVE_COLS = [
  ['站名', '站名'], ['省份', '省份'], ['流域', '流域'], ['水质类别', '类别'],
  ['时间', '监测时间'], ['水温', '水温'], ['pH', 'pH'], ['溶解氧', '溶解氧'],
  ['氨氮', '氨氮'], ['总磷', '总磷'], ['总氮', '总氮'], ['浊度', '浊度'],
]
const OFFLINE_COLS = [
  ['站名', '站名'], ['省份', '省份'], ['流域', '流域'], ['城市', '城市'],
  ['水质目标', '目标'], ['距离_km', '距离(km)'], ['lat', '纬度'], ['lon', '经度'],
]

const cols = () => (data.value?.['含实时监测值'] ? LIVE_COLS : OFFLINE_COLS)

/** 水质类别着色（Ⅰ~劣Ⅴ）。 */
function classColor(c) {
  return { I: '#2E8B57', II: '#00A36C', III: '#FFA500', IV: '#FF4500', V: '#DC143C', 劣V: '#8B0000' }[c] || '#56687a'
}
</script>

<template>
  <div class="wrap">
    <div class="bar">
      <select v-model="province" :disabled="loading">
        <option v-for="p in provinces" :key="p" :value="p">{{ p }}</option>
      </select>
      <input v-model="keyword" placeholder="站名/流域关键词（可选），如 珠江" :disabled="loading" />
      <label class="switch">
        <input v-model="live" type="checkbox" :disabled="loading" />
        <span>实时数据</span>
      </label>
      <button :disabled="loading" @click="run">{{ loading ? '查询中…' : '查询' }}</button>
    </div>

    <p class="tip">
      实时模式需选择省份（全量拉取会让政府服务器超时）；查不到时自动降级到内置离线站点库。
    </p>

    <template v-if="data">
      <div class="meta">
        <span class="chip">{{ data['站点数量'] }} 个站点</span>
        <span class="chip">{{ data['数据来源'] }}</span>
        <span v-if="data['数据时效']" class="chip">{{ data['数据时效'] }}</span>
      </div>

      <p v-if="data['提示']" class="note">{{ data['提示'] }}</p>
      <p v-if="data['权威性']" class="note">{{ data['权威性'] }}</p>

      <div v-if="(data['站点列表'] || []).length" class="table-wrap">
        <table>
          <thead>
            <tr><th v-for="[k, label] in cols()" :key="k">{{ label }}</th></tr>
          </thead>
          <tbody>
            <tr v-for="(s, i) in data['站点列表']" :key="i">
              <td v-for="[k] in cols()" :key="k">
                <span
                  v-if="k === '水质类别'"
                  class="badge" :style="{ background: classColor(s[k]) }"
                >{{ s[k] || '—' }}</span>
                <template v-else>{{ s[k] ?? '—' }}</template>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="note">未找到匹配站点。</p>
    </template>
  </div>
</template>

<style scoped>
.bar { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 8px; }
select, input[type="text"], input:not([type]) {
  background: var(--c-surface-2); border: 1px solid var(--c-border); color: var(--c-ink);
  border-radius: 7px; padding: 7px 11px; font-family: inherit; font-size: 13px;
}
input:not([type]) { flex: 1; min-width: 200px; }
.switch { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--c-sub); cursor: pointer; }
button {
  background: var(--c-accent); color: #06121c; border: none;
  padding: 8px 20px; border-radius: 7px; font-weight: 600; font-size: 13px;
}
button:disabled { opacity: .5; cursor: not-allowed; }

.tip { margin: 0 0 12px; font-size: 11px; color: var(--c-sub); }

.meta { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.chip {
  font-size: 11px; color: var(--c-sub); background: var(--c-surface-2);
  border: 1px solid var(--c-border); padding: 3px 10px; border-radius: 20px;
}
.note { font-size: 11px; color: var(--c-sub); margin: 4px 0; }

.table-wrap { max-height: 380px; overflow: auto; border: 1px solid var(--c-border); border-radius: 8px; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th {
  position: sticky; top: 0; background: var(--c-surface-2);
  padding: 8px 10px; text-align: left; font-weight: 600;
  color: var(--c-sub); border-bottom: 1px solid var(--c-border); white-space: nowrap;
}
td { padding: 6px 10px; border-bottom: 1px solid #1a242e; white-space: nowrap; }
tr:hover td { background: #1a2530; }
.badge { padding: 1px 8px; border-radius: 20px; color: #fff; font-size: 11px; font-weight: 600; }
</style>
