<script setup>
/** 数据状态横幅 —— 对应 app.py 的 _tech_data_status。 */
import { computed } from 'vue'
import { useSessionStore } from '../stores/session'

const store = useSessionStore()
const items = computed(() => [
  store.status.has_data
    ? { ok: true, text: `走航数据 ${store.status.rows} 条` }
    : { ok: false, text: '走航数据未加载' },
  store.status.video_frames
    ? { ok: true, text: `视频 ${store.status.video_frames} 帧` }
    : { ok: false, text: '视频未上传' },
  store.status.has_black_spots
    ? { ok: true, text: `黑臭检测完成 ${store.status.black_spot_count} 个黑点` }
    : { ok: false, text: '黑臭检测未运行' },
  { ok: true, text: `目标水质 ${store.status.target_water_class} 类` },
])
</script>

<template>
  <div class="banner">
    <span v-for="(it, i) in items" :key="i" class="chip" :class="{ ok: it.ok }">
      <i></i>{{ it.text }}
    </span>
  </div>
</template>

<style scoped>
.banner { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 6px; }
.chip {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 12px; color: var(--c-sub);
  background: var(--c-surface-2); border: 1px solid var(--c-border);
  padding: 4px 11px; border-radius: 20px;
}
.chip i { width: 6px; height: 6px; border-radius: 50%; background: #56687a; }
.chip.ok { color: var(--c-ink); border-color: #1d5b4f; }
.chip.ok i { background: var(--c-accent-2); }
</style>
