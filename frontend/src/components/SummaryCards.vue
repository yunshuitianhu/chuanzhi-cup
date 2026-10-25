<script setup>
/** 数据统计指标卡 —— 对应 app.py 的 tool_data_summary 展示。 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../api/client'
import { useSessionStore } from '../stores/session'
import { toastErr } from '../composables/useToast'

const store = useSessionStore()
const data = ref(null)
const loading = ref(false)
const error = ref('')
const FIELDS = ['氨氮', '溶解氧', '浊度', 'pH', 'COD']
let requestVersion = 0

async function load() {
  const version = ++requestVersion
  data.value = null
  error.value = ''
  if (store.switchingProject || !store.status.has_data) { loading.value = false; return }
  loading.value = true
  try {
    const result = await api.summary()
    if (version === requestVersion) data.value = result
  } catch (e) {
    if (version === requestVersion) {
      error.value = `统计加载失败：${e.message}`
      toastErr(error.value)
    }
  } finally {
    if (version === requestVersion) loading.value = false
  }
}

onMounted(load)
onBeforeUnmount(() => { requestVersion++ })
watch(() => [store.projectState.current?.id, store.workspaceRevision, store.switchingProject,
  store.status.has_data, store.status.rows, store.status.analysis_revision], load, {flush:'sync'})
</script>

<template>
  <div v-if="!data" class="tip" :role="error ? 'alert' : undefined">{{ error || (loading ? '正在加载统计…' : store.switchingProject ? '正在切换项目…' : '需先上传走航数据') }}</div>
  <div v-else class="cards">
    <div class="card">
      <div class="k">记录数</div>
      <div class="v">{{ data['记录数'] }}</div>
      <div class="s">{{ String(data['时间范围'] || '').slice(0, 16) }}</div>
    </div>
    <div v-for="key in FIELDS" v-show="data[key]" :key="key" class="card">
      <div class="k">{{ key }}</div>
      <div class="v">
        {{ data[key]?.['均值'] ?? '—' }}<small>{{ data[key]?.['单位'] }}</small>
      </div>
      <div class="s">极值 {{ data[key]?.['极值'] ?? '—' }}</div>
    </div>
  </div>
</template>

<style scoped>
.tip { font-size: 12px; color: var(--c-sub); }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; }
.card {
  background: var(--c-surface-2); border: 1px solid var(--c-border);
  border-radius: 8px; padding: 10px 13px;
}
.k { font-size: 11px; color: var(--c-sub); }
.v { font-size: 20px; font-weight: 700; color: var(--c-accent); margin: 2px 0; }
.v small { font-size: 11px; color: var(--c-sub); margin-left: 3px; font-weight: 400; }
.s { font-size: 11px; color: var(--c-sub); }
</style>
