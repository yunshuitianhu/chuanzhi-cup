<script setup>
/**
 * RAG 知识库检索 —— 对应 app.py tab4 的第 5 区。
 *
 * 本地整理的治理参考资料，经向量与词面融合后按来源合并。
 * 对话和报告共用检索内核，具体引用须核对片段；原始发布内容未独立核验。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../api/client'
import { toastErr, toastOk } from '../composables/useToast'
import RagCase from './RagCase.vue'
import {useSessionStore} from '../stores/session'
import {ragStageText} from '../utils/ragStage.js'
const store=useSessionStore()
const locked=computed(()=>loading.value||store.loading||store.chatRunning||store.switchingProject||!store.ready)

const status = ref(null)
const query = ref('')
const topK = ref(5)
const loading = ref(false)
const data = ref(null)
const statusLoading = ref(false)
let disposed=false, statusRequest=0, searchRequest=0
onBeforeUnmount(()=>{disposed=true;statusRequest++;searchRequest++})

const QUICK = ['黑臭水体治理', '氨氮工业废水处理', '富营养化湖泊修复', '城市河道综合治理']

/**
 * 输入为空时使用的兜底查询。
 *
 * ⚠️ 这里**不能**把按钮设成 disabled。原 app.py 的行为是
 * `query = tech_rag_query or "黑臭水体治理"` —— 空着也能点，走默认查询。
 * 之前本组件写成「空输入即禁用」，结果是：按钮看着是亮的、点下去没反应，
 * 用户完全不知道该怎么继续（已由用户实际反馈）。
 * 一个看不出原因的死按钮，比一个用默认值兜底的按钮差得多。
 */
const DEFAULT_QUERY = '黑臭水体治理'

onMounted(loadStatus)

async function loadStatus() {
  const ticket=++statusRequest
  statusLoading.value=true
  try {
    const value=await api.ragStatus()
    if(!disposed&&ticket===statusRequest)status.value=value
  } catch (e) {
    if(!disposed&&ticket===statusRequest)status.value={available:false,error:e.message}
  } finally {
    if(ticket===statusRequest)statusLoading.value=false
  }
}

function selectionContext() {
  const workspace=store.workspaceRevision,project=store.projectState.current?.id,
    revision=store.status.analysis_revision,results=store.status.results_revision,
    text=query.value,k=topK.value
  return ()=>!disposed&&!store.switchingProject&&workspace===store.workspaceRevision
    &&project===store.projectState.current?.id&&revision===store.status.analysis_revision
    &&results===store.status.results_revision&&text===query.value&&k===topK.value
}
watch(()=>[store.workspaceRevision,store.projectState.current?.id,store.status.analysis_revision,
  store.status.results_revision,store.switchingProject,query.value,topK.value],()=>{
  searchRequest++;data.value=null
},{flush:'sync'})
async function run(q) {
  if(disposed||locked.value)return
  const text=(q||query.value).trim()||DEFAULT_QUERY
  query.value=text
  const ticket=++searchRequest,current=selectionContext()
  data.value=null;loading.value=true
  try {
    const result=await store._run(()=>api.ragSearch(text,topK.value))
    if(ticket!==searchRequest||!current())return
    data.value=result
    await loadStatus()
    if(ticket===searchRequest&&current())toastOk(`检索到 ${result['共检索到']} 份参考资料`)
  } catch (e) {
    if(ticket===searchRequest&&current()){
      await loadStatus()
      if(ticket===searchRequest&&current())toastErr(`检索失败：${e.message}`)
    }
  } finally {loading.value=false}
}

</script>

<template>
  <div class="wrap">
    <!-- 知识库状态 -->
    <div v-if="status" class="status">
      <template v-if="status.available">
        <span class="ok">● {{status.retrieval_verified ? '检索已验证' : '索引可读，检索待验证'}}</span>
        <span class="dim">阶段：{{ragStageText(status.stage)}}</span>
        <span class="dim">{{ status.source_documents }} 份资料 · {{ status.chunks }} 个向量块</span>
        <span class="dim">{{ status.embedding_model }}</span>
      </template>
      <template v-else>
        <span class="bad">● 知识库不可用</span>
        <span class="dim">阶段：{{ragStageText(status.stage)}}</span>
        <span class="dim">{{ status.notice || status.error || '尚未核对索引、依赖与模型，请重新检查。' }}</span>
      </template>
      <span v-if="status.available && status.notice" class="dim">{{status.notice}}</span>
      <button class="qbtn" :disabled="statusLoading || loading" @click="loadStatus">{{statusLoading?'检查中…':'重新检查知识库'}}</button>
    </div>

    <!-- 检索 -->
    <div class="bar">
      <input
        v-model="query"
        placeholder="例如：氨氮超标 黑臭水体 城市河道治理"
        aria-label="治理资料查询"
        maxlength="1000"
        :disabled="locked"
        @keyup.enter="run()"
      />
      <label class="topk">
        返回
        <input v-model.number="topK" type="number" min="1" max="10" :disabled="locked" />
        个
      </label>
      <button
        :disabled="locked || !status?.available"
        :title="status?.available ? '' : '知识库不可用'"
        @click="run()"
      >
        {{ loading ? '检索中…' : '检索相似案例' }}
      </button>
    </div>

    <div class="quick">
      <button v-for="q in QUICK" :key="q" class="qbtn" :disabled="locked" @click="run(q)">{{ q }}</button>
    </div>

    <!-- 结果 -->
    <template v-if="data">
      <p class="note">查询：{{ data['查询'] }} · 返回 {{ data['共检索到'] }} 份按来源文件合并的参考资料。近似匹配不代表资料包含所问答案；相似度不是治理效果或责任概率。</p>
      <div class="cards">
        <RagCase v-for="(c,i) in data['检索结果']" :key="c['来源文件']||i" :item="c" :retrieval-id="data.retrieval_id || ''" />
      </div>
    </template>
  </div>
</template>

<style scoped>
.status {
  display: flex; gap: 12px; flex-wrap: wrap; align-items: center;
  font-size: 14px; margin-bottom: 12px;
  padding: 8px 12px; background: var(--c-surface-2); border-radius: 7px;
}
.ok { color: var(--c-accent-2); font-weight: 600; }
.bad { color: #ff6b81; font-weight: 600; }
.dim { color: var(--c-sub); }

.bar { display: flex; gap: 10px; align-items: center; margin-bottom: 10px; }
.bar > input {
  flex: 1; background: var(--c-surface-2); border: 1px solid var(--c-border);
  color: var(--c-ink); border-radius: 8px; padding: 9px 13px; font-family: inherit; font-size: 15px;
}
.bar > input:focus { outline: none; border-color: var(--c-accent); }
.topk { display: flex; align-items: center; gap: 5px; font-size: 14px; color: var(--c-sub); white-space: nowrap; }
.topk input {
  width: 52px; background: var(--c-surface-2); border: 1px solid var(--c-border);
  color: var(--c-ink); border-radius: 6px; padding: 6px 8px; font-family: inherit;
}
button {
  background: var(--c-accent); color: #06121c; border: none;
  padding: 9px 20px; border-radius: 8px; font-weight: 600; font-size: 15px; white-space: nowrap;
}
button:disabled { opacity: .45; cursor: not-allowed; }

.quick { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; }
.qbtn {
  background: transparent; border: 1px solid var(--c-border); color: var(--c-sub);
  padding: 5px 13px; border-radius: 20px; font-size: 14px; font-weight: 400;
}
.qbtn:hover:not(:disabled) { border-color: var(--c-accent); color: var(--c-ink); }

.note { font-size: 14px; color: var(--c-sub); margin: 0 0 10px; }

.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; }
.case {
  background: var(--c-surface-2); border: 1px solid var(--c-border);
  border-radius: 9px; padding: 13px 15px;
}
.case header { display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; }
.case h4 { margin: 0; font-size: 15px; line-height: 1.5; }
.sim { border: 1px solid; border-radius: 20px; padding: 1px 9px; font-size: 14px; font-weight: 600; flex-shrink: 0; }
.tags { display: flex; gap: 10px; flex-wrap: wrap; margin: 6px 0; font-size: 14px; color: var(--c-sub); }
.methods { margin: 6px 0 0; font-size: 14px; color: var(--c-sub); line-height: 1.7; }
details { margin-top: 8px; font-size: 14px; }
details summary { cursor: pointer; color: var(--c-sub); }
.summary { margin: 6px 0 0; color: var(--c-sub); line-height: 1.8; white-space: pre-wrap; }
</style>
