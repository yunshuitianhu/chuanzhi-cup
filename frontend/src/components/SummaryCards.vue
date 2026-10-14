<script setup>
/** 数据统计指标卡 —— 对应 app.py 的 tool_data_summary 展示。 */
import { onMounted, ref, watch } from 'vue'
import { api } from '../api/client'
import { useSessionStore } from '../stores/session'
import { toastErr } from '../composables/useToast'

const store = useSessionStore()
const data = ref(null)
const FIELDS = ['氨氮', '溶解氧', '浊度', 'pH', 'COD']

async function load() {
  if (!store.status.has_data) { data.value = null; return }
  try {
    data.value = await api.summary()
  } catch (e) {
    toastErr(`统计加载失败：${e.message}`)
  }
}

onMounted(load)
watch(() => store.status.rows, load)
</script>

<template>
  <div v-if="!data" class="tip">需先上传走航数据</div>
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
