<script setup>
import { computed, ref } from 'vue'
import { useSessionStore } from '../stores/session'
import { toastErr, toastOk } from '../composables/useToast'

const store = useSessionStore()
const name = ref('')
const selected = ref('')
const fileInput = ref(null)
const busy = computed(() => store.loading || store.chatRunning || !store.ready)
const current = computed(() => store.projectState.current)

async function run(action, message) {
  try { await action(); toastOk(message) }
  catch (e) { toastErr(e.message) }
}
async function create(fromCurrent) {
  if (!name.value.trim()) { toastErr('请填写项目名称'); return }
  await run(async () => {
    await store.createProject(name.value.trim(), fromCurrent)
    name.value = ''; selected.value = ''
  }, fromCurrent ? '当前内容已保存为新项目' : '空项目已创建，原内容已保留')
}
async function upload(e) {
  const file = e.target.files?.[0]
  if (!file) return
  await run(() => store.importProject(file), '项目已导入为独立副本')
  e.target.value = ''
}
async function download() {
  await run(async () => {
    const blob = await store.exportProject()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url; link.download = `river-project-${current.value.id}.wqproj`
    document.body.appendChild(link); link.click(); link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }, '已保存并导出项目包')
}
</script>

<template>
  <section class="project-panel">
    <h3>河流项目</h3>
    <div class="current">{{ current?.name || '临时会话 · 尚未保存' }}</div>
    <p v-if="current" class="note">已保存版本 {{ current.revision }} · {{ new Date(current.updated_at).toLocaleString() }}</p>
    <p v-if="store.projectState.restore_error" class="restore-error">{{ store.projectState.restore_error }}</p>
    <div class="buttons">
      <button :disabled="busy || !current" @click="run(() => store.saveProject(), '项目已保存')">保存项目</button>
      <button :disabled="busy || !current" @click="download">保存并导出</button>
    </div>
    <label class="label" for="project-name">新项目名称</label>
    <input id="project-name" v-model="name" maxlength="100" :disabled="busy" placeholder="例如：本次巡航调查" />
    <div class="buttons">
      <button :disabled="busy" @click="create(true)">当前内容另存为</button>
      <button :disabled="busy" @click="create(false)">新建空项目</button>
    </div>
    <label class="label" for="project-select">打开已保存项目</label>
    <select id="project-select" v-model="selected" :disabled="busy">
      <option value="">请选择项目</option>
      <option v-for="p in store.projectState.projects" :key="p.id" :value="p.id">{{ p.name }} · v{{ p.revision }}</option>
    </select>
    <div class="buttons">
      <button :disabled="busy || !selected || selected === current?.id" @click="run(() => store.openProject(selected), '项目已打开')">打开项目</button>
      <button :disabled="busy" @click="fileInput.click()">导入项目包</button>
    </div>
    <input ref="fileInput" type="file" accept=".wqproj,.zip" hidden @change="upload" />
    <p class="note">修改后请保存；切换前自动保存。临时会话会先存为未命名项目。导入保留原项目，密钥在本机单独配置。</p>
  </section>
</template>

<style scoped>
.project-panel { background:var(--c-surface-2); border:1px solid var(--c-border); border-radius:8px; padding:12px; }
h3 { margin:0 0 10px; font-size:12px; color:var(--c-sub); }
.current { font-size:13px; font-weight:600; overflow-wrap:anywhere; }
.label { display:block; font-size:11px; color:var(--c-sub); margin:12px 0 5px; }
input:not([type=file]), select { width:100%; box-sizing:border-box; padding:7px; border-radius:5px; border:1px solid var(--c-border); background:var(--c-surface); color:var(--c-ink); font:inherit; font-size:12px; }
.buttons { display:flex; gap:6px; margin-top:8px; }
button { flex:1; padding:7px 3px; border:1px solid var(--c-border); border-radius:5px; background:var(--c-surface); color:var(--c-ink); font-size:11px; cursor:pointer; }
button:hover:not(:disabled) { border-color:var(--c-accent); }
button:disabled { opacity:.45; cursor:default; }
.note { font-size:11px; line-height:1.6; color:var(--c-sub); margin:8px 0 0; }
.restore-error { font-size:12px; color:#ffb3c1; }
</style>
