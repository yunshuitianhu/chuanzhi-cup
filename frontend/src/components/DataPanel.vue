<script setup>
/**
 * 数据上传面板 —— 对应 app.py 侧边栏的「数据上传与配置」区。
 *
 * 交互与原版一致：
 *   1. 选文件（走航数据必选，视频可选）
 *   2. 点「加载数据」才真正上传并解析
 * 之所以不在选择文件后立即上传：大视频上传耗时，原版特意让用户
 * 「等文件名出现后再点加载」，避免误以为卡住。这里保留该节奏。
 */
import { ref, computed } from 'vue'
import { useSessionStore } from '../stores/session'
import { toastOk, toastErr } from '../composables/useToast'

const store = useSessionStore()

const dataFile = ref(null)
const videoFile = ref(null)
const dataInput = ref(null)
const videoInput = ref(null)
const report = ref('')

const canLoad = computed(() => !!dataFile.value && !store.loading)

function onPick(kind, e) {
  const f = e.target.files?.[0] || null
  if (kind === 'data') dataFile.value = f
  else videoFile.value = f
}

function clearPick(kind) {
  if (kind === 'data') { dataFile.value = null; if (dataInput.value) dataInput.value.value = '' }
  else { videoFile.value = null; if (videoInput.value) videoInput.value.value = '' }
}

async function load() {
  if (!dataFile.value) return
  try {
    const res = await store.uploadData(dataFile.value)
    report.value = res.report
    toastOk(`数据加载完成：${res.rows} 条记录`)
    if (videoFile.value) {
      const v = await store.uploadVideo(videoFile.value)
      toastOk(`视频抽帧完成：${v.frames} 帧`)
    }
  } catch (e) {
    toastErr(`加载失败：${e.message}`)
  }
}
</script>

<template>
  <section class="panel">
    <h3 class="panel-title">数据上传</h3>

    <label class="file-btn" :class="{ filled: dataFile }">
      <input
        ref="dataInput" type="file" accept=".xlsx,.xls,.csv" hidden
        @change="onPick('data', $event)"
      />
      <span class="ico">📄</span>
      <span class="txt">{{ dataFile ? dataFile.name : '选择走航数据 (xlsx/xls/csv)' }}</span>
      <button v-if="dataFile" class="clear" @click.prevent="clearPick('data')" title="移除">×</button>
    </label>

    <label class="file-btn" :class="{ filled: videoFile }">
      <input
        ref="videoInput" type="file" accept=".mp4,.avi,.mov" hidden
        @change="onPick('video', $event)"
      />
      <span class="ico">🎥</span>
      <span class="txt">{{ videoFile ? videoFile.name : '选择样本视频 (可选)' }}</span>
      <button v-if="videoFile" class="clear" @click.prevent="clearPick('video')" title="移除">×</button>
    </label>

    <button class="load-btn" :disabled="!canLoad" @click="load">
      {{ store.loading ? '处理中…' : '加载数据' }}
    </button>

    <p v-if="videoFile" class="hint">
      ⚠ 大视频上传需要时间，请等文件名出现后再点「加载数据」。
    </p>

    <div v-if="store.status.has_data" class="stat">
      <div class="stat-row"><span>走航数据</span><b>{{ store.status.rows }} 条</b></div>
      <div class="stat-row"><span>视频帧</span><b>{{ store.status.video_frames }} 帧</b></div>
      <div class="stat-row" v-if="store.status.time_range">
        <span>时段</span>
        <b class="small">{{ store.status.time_range[0].slice(5, 16) }} ~ {{ store.status.time_range[1].slice(11, 16) }}</b>
      </div>
    </div>

    <details v-if="report" class="report">
      <summary>加载详情</summary>
      <pre>{{ report }}</pre>
    </details>
  </section>
</template>

<style scoped>
.panel {
  background: var(--c-surface-2); border: 1px solid var(--c-border);
  border-radius: 8px; padding: 12px;
}
.panel-title {
  margin: 0 0 10px; font-size: 12px; font-weight: 600;
  color: var(--c-sub); letter-spacing: .5px; text-transform: uppercase;
}

.file-btn {
  display: flex; align-items: center; gap: 8px;
  border: 1px dashed var(--c-border); border-radius: 7px;
  padding: 9px 10px; margin-bottom: 8px; cursor: pointer;
  font-size: 12px; color: var(--c-sub); transition: all .15s;
}
.file-btn:hover { border-color: var(--c-accent); color: var(--c-ink); }
.file-btn.filled { border-style: solid; border-color: #1d5b4f; color: var(--c-ink); }
.ico { flex-shrink: 0; }
.txt { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.clear {
  background: transparent; border: none; color: var(--c-sub);
  font-size: 16px; line-height: 1; padding: 0 2px;
}
.clear:hover { color: #ff6b81; }

.load-btn {
  width: 100%; padding: 9px; border-radius: 7px; border: none;
  background: var(--c-accent); color: #06121c; font-weight: 600;
  transition: opacity .15s;
}
.load-btn:disabled { opacity: .4; cursor: not-allowed; }
.load-btn:not(:disabled):hover { opacity: .88; }

.hint { margin: 8px 0 0; font-size: 11px; color: var(--c-sub); line-height: 1.5; }

.stat { margin-top: 10px; border-top: 1px solid var(--c-border); padding-top: 8px; }
.stat-row {
  display: flex; justify-content: space-between; align-items: baseline;
  font-size: 12px; padding: 2px 0; color: var(--c-sub);
}
.stat-row b { color: var(--c-ink); font-weight: 600; }
.stat-row b.small { font-size: 11px; font-weight: 400; }

.report { margin-top: 8px; font-size: 11px; }
.report summary { cursor: pointer; color: var(--c-sub); }
.report pre {
  margin: 6px 0 0; padding: 8px; background: #0d151d; border-radius: 6px;
  white-space: pre-wrap; word-break: break-all; color: var(--c-sub); font-size: 11px;
}
</style>
