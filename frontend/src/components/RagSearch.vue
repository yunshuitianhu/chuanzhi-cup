<script setup>
/**
 * RAG 知识库检索 —— 对应 app.py tab4 的第 5 区。
 *
 * 29 个真实治理案例，经 text2vec-base-chinese 嵌入后存 ChromaDB，
 * 按余弦相似度检索。报告生成时也会自动走同一条链路注入参考案例。
 */
import { onMounted, ref } from 'vue'
import { api } from '../api/client'
import { toastErr, toastOk } from '../composables/useToast'

const status = ref(null)
const query = ref('')
const topK = ref(5)
const loading = ref(false)
const data = ref(null)

const QUICK = ['黑臭水体治理', '氨氮工业废水处理', '富营养化湖泊修复', '城市河道综合治理']

onMounted(loadStatus)

async function loadStatus() {
  try {
    status.value = await api.ragStatus()
  } catch (e) {
    status.value = { available: false, error: e.message }
  }
}

async function run(q) {
  const text = (q || query.value).trim()
  if (!text) return
  query.value = text
  loading.value = true
  try {
    data.value = await api.ragSearch(text, topK.value)
    toastOk(`检索到 ${data.value['共检索到']} 个相似案例`)
  } catch (e) {
    toastErr(`检索失败：${e.message}`)
  } finally {
    loading.value = false
  }
}

/** 相似度 → 颜色（越高越绿）。阈值取自 SKILL 文档的经验区间。 */
function simColor(s) {
  if (s >= 0.8) return '#2E8B57'
  if (s >= 0.6) return '#FFA500'
  return '#8495AA'
}
</script>

<template>
  <div class="wrap">
    <!-- 知识库状态 -->
    <div v-if="status" class="status">
      <template v-if="status.available">
        <span class="ok">● 知识库就绪</span>
        <span class="dim">{{ status.source_documents }} 个案例 · {{ status.chunks }} 个向量块</span>
        <span class="dim">{{ status.embedding_model }}</span>
      </template>
      <template v-else>
        <span class="bad">● 知识库不可用</span>
        <span class="dim">{{ status.error || '索引未构建，请运行 python scripts/build_rag_index.py' }}</span>
      </template>
    </div>

    <!-- 检索 -->
    <div class="bar">
      <input
        v-model="query"
        placeholder="例如：氨氮超标 黑臭水体 城市河道治理"
        :disabled="loading"
        @keyup.enter="run()"
      />
      <label class="topk">
        返回
        <input v-model.number="topK" type="number" min="1" max="10" :disabled="loading" />
        个
      </label>
      <button :disabled="!query.trim() || loading" @click="run()">
        {{ loading ? '检索中…' : '检索相似案例' }}
      </button>
    </div>

    <div class="quick">
      <button v-for="q in QUICK" :key="q" class="qbtn" :disabled="loading" @click="run(q)">{{ q }}</button>
    </div>

    <!-- 结果 -->
    <template v-if="data">
      <p class="note">查询：{{ data['查询'] }} · 命中 {{ data['共检索到'] }} 个案例</p>
      <div class="cards">
        <article v-for="(c, i) in data['检索结果']" :key="i" class="case">
          <header>
            <h4>{{ c['标题'] }}</h4>
            <span class="sim" :style="{ borderColor: simColor(c['相似度']), color: simColor(c['相似度']) }">
              {{ (c['相似度'] * 100).toFixed(0) }}%
            </span>
          </header>
          <div class="tags">
            <span v-if="c['地点']">📍 {{ c['地点'] }}</span>
            <span v-if="c['污染程度']">🏷️ {{ c['污染程度'] }}</span>
          </div>
          <p v-if="c['治理方法']" class="methods">治理方法：{{ c['治理方法'] }}</p>
          <details v-if="c['内容摘要']">
            <summary>查看摘要</summary>
            <p class="summary">{{ c['内容摘要'] }}</p>
          </details>
        </article>
      </div>
    </template>
  </div>
</template>

<style scoped>
.status {
  display: flex; gap: 12px; flex-wrap: wrap; align-items: center;
  font-size: 12px; margin-bottom: 12px;
  padding: 8px 12px; background: var(--c-surface-2); border-radius: 7px;
}
.ok { color: var(--c-accent-2); font-weight: 600; }
.bad { color: #ff6b81; font-weight: 600; }
.dim { color: var(--c-sub); }

.bar { display: flex; gap: 10px; align-items: center; margin-bottom: 10px; }
.bar > input {
  flex: 1; background: var(--c-surface-2); border: 1px solid var(--c-border);
  color: var(--c-ink); border-radius: 8px; padding: 9px 13px; font-family: inherit; font-size: 13px;
}
.bar > input:focus { outline: none; border-color: var(--c-accent); }
.topk { display: flex; align-items: center; gap: 5px; font-size: 12px; color: var(--c-sub); white-space: nowrap; }
.topk input {
  width: 52px; background: var(--c-surface-2); border: 1px solid var(--c-border);
  color: var(--c-ink); border-radius: 6px; padding: 6px 8px; font-family: inherit;
}
button {
  background: var(--c-accent); color: #06121c; border: none;
  padding: 9px 20px; border-radius: 8px; font-weight: 600; font-size: 13px; white-space: nowrap;
}
button:disabled { opacity: .45; cursor: not-allowed; }

.quick { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; }
.qbtn {
  background: transparent; border: 1px solid var(--c-border); color: var(--c-sub);
  padding: 5px 13px; border-radius: 20px; font-size: 12px; font-weight: 400;
}
.qbtn:hover:not(:disabled) { border-color: var(--c-accent); color: var(--c-ink); }

.note { font-size: 12px; color: var(--c-sub); margin: 0 0 10px; }

.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; }
.case {
  background: var(--c-surface-2); border: 1px solid var(--c-border);
  border-radius: 9px; padding: 13px 15px;
}
.case header { display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; }
.case h4 { margin: 0; font-size: 13px; line-height: 1.5; }
.sim { border: 1px solid; border-radius: 20px; padding: 1px 9px; font-size: 11px; font-weight: 600; flex-shrink: 0; }
.tags { display: flex; gap: 10px; flex-wrap: wrap; margin: 6px 0; font-size: 11px; color: var(--c-sub); }
.methods { margin: 6px 0 0; font-size: 11px; color: var(--c-sub); line-height: 1.7; }
details { margin-top: 8px; font-size: 11px; }
details summary { cursor: pointer; color: var(--c-sub); }
.summary { margin: 6px 0 0; color: var(--c-sub); line-height: 1.8; white-space: pre-wrap; }
</style>
