<script setup>
/**
 * 应用外壳：侧边栏（配置 + 数据） + 顶部标签导航 + 路由视图。
 *
 * 结构对应 app.py：侧边栏 = st.sidebar，标签导航 = st.tabs 的四个页签。
 */
import { onMounted, ref, computed } from 'vue'
import { useRoute } from 'vue-router'
import { useSessionStore } from './stores/session'
import { routes } from './router'
import StatusBadge from './components/StatusBadge.vue'
import DataPanel from './components/DataPanel.vue'
import ProjectPanel from './components/ProjectPanel.vue'
import Toast from './components/Toast.vue'

const store = useSessionStore()
const route = useRoute()
const navItems = routes.filter((r) => r.meta)

onMounted(() => store.bootstrap())

const keySummary = computed(() => {
  const k = store.config.keys || {}
  return [
    { name: 'DeepSeek', ok: !!k.deepseek, required: true },
    { name: '高德地图', ok: !!k.amap, required: false },
    { name: '星图地球', ok: !!k.starcloud, required: false },
  ]
})
</script>

<template>
  <div class="shell">
    <!-- ─────────── 侧边栏 ─────────── -->
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-title">水质监测与污染溯源助手</div>
        <div class="brand-sub">AI Web 应用 · Vue 3 + FastAPI</div>
      </div>

      <ProjectPanel />

      <section class="panel">
        <h3 class="panel-title">密钥状态</h3>
        <div class="key-row" v-for="k in keySummary" :key="k.name">
          <span class="dot" :class="{ ok: k.ok }"></span>
          <span class="key-name">{{ k.name }}</span>
          <span class="key-tag">{{ k.ok ? '已配置' : (k.required ? '必填' : '可选') }}</span>
        </div>
        <p class="hint">
          密钥通过后端 <code>.env</code> 配置，前端不接触、不存储密钥。
        </p>
      </section>

      <section class="panel">
        <h3 class="panel-title">目标水质等级</h3>
        <div class="class-picker">
          <button
            v-for="c in (store.config.valid_classes || [])"
            :key="c"
            class="class-btn"
            :class="{ active: store.config.target_water_class === c }"
            :disabled="store.loading || store.chatRunning"
            @click="store.setTargetClass(c)"
          >{{ c }}</button>
        </div>
        <p class="hint">
          黑臭检测以此等级为基线计算超标倍数。切换会清空已有分析结果。
        </p>
      </section>

      <DataPanel :key="store.workspaceRevision" />

      <footer class="side-foot">
        <StatusBadge :ok="store.ready" :text="store.ready ? '后端已连接' : '后端未连接'" />
      </footer>
    </aside>

    <!-- ─────────── 主区 ─────────── -->
    <main class="main">
      <nav class="tabs">
        <RouterLink
          v-for="item in navItems"
          :key="item.path"
          :to="item.path"
          class="tab"
          :class="{ active: route.path === item.path }"
        >
          <span class="tab-icon">{{ item.meta.icon }}</span>
          <span>{{ item.meta.title }}</span>
        </RouterLink>

        <div class="tabs-right">
          <template v-if="store.status.has_data">
            <span class="pill">数据 {{ store.status.rows }} 条</span>
            <span v-if="store.status.video_frames" class="pill">视频 {{ store.status.video_frames }} 帧</span>
            <span v-if="store.status.black_spot_count" class="pill warn">
              黑点 {{ store.status.black_spot_count }}
            </span>
          </template>
          <span v-else class="pill muted">未加载数据</span>
        </div>
      </nav>

      <div class="content">
        <section v-if="!store.ready" class="connection-panel" aria-live="polite">
          <h2>{{ store.connecting ? '正在连接项目服务' : '项目服务尚未就绪' }}</h2>
          <p v-if="store.connecting">{{ store.bootstrapStage }}…</p>
          <template v-else>
            <p>在项目目录运行 <code>python scripts/serve_web.py</code>，或双击 <code>start_web.cmd</code>，并保持终端运行。</p>
            <p>启动入口默认提供 <code>http://127.0.0.1:5174/</code>；自定义端口时请打开终端显示的地址。开发模式仍需同时运行前端和后端。</p>
            <p>若下方提示项目目录或数据恢复失败，请按对应阶段排查。已有项目不会因点击重连而被删除。</p>
          </template>
          <button :disabled="store.connecting || store.loading || store.chatRunning || store.switchingProject" @click="store.bootstrap()">
            {{ store.connecting ? '连接中…' : '重新连接' }}
          </button>
        </section>
        <div v-if="store.error" class="error-bar">
          <strong>出错了：</strong>{{ store.error }}
        </div>
        <RouterView v-if="store.ready && !store.switchingProject" :key="store.workspaceRevision" />
      </div>
    </main>

    <Toast />
  </div>
</template>

<style scoped>
.shell { display: flex; min-height: 100vh; }

/* ── 侧边栏 ── */
.sidebar {
  width: 292px; flex-shrink: 0;
  background: var(--c-surface);
  border-right: 1px solid var(--c-border);
  padding: 18px 16px;
  display: flex; flex-direction: column; gap: 16px;
  position: sticky; top: 0; height: 100vh; overflow-y: auto;
}
.brand-title { font-size: 16px; font-weight: 600; letter-spacing: .3px; }
.brand-sub { font-size: 11px; color: var(--c-sub); margin-top: 2px; }

.panel {
  background: var(--c-surface-2);
  border: 1px solid var(--c-border);
  border-radius: 8px; padding: 12px;
}
.panel-title {
  margin: 0 0 10px; font-size: 12px; font-weight: 600;
  color: var(--c-sub); letter-spacing: .5px; text-transform: uppercase;
}

.key-row { display: flex; align-items: center; gap: 8px; padding: 3px 0; font-size: 13px; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: #56687a; flex-shrink: 0; }
.dot.ok { background: var(--c-accent-2); box-shadow: 0 0 6px var(--c-accent-2); }
.key-name { flex: 1; }
.key-tag { font-size: 11px; color: var(--c-sub); }

.hint { margin: 10px 0 0; font-size: 11px; color: var(--c-sub); line-height: 1.5; }
.hint code { background: #0d151d; padding: 1px 4px; border-radius: 3px; }

.class-picker { display: flex; gap: 6px; }
.class-btn {
  flex: 1; padding: 6px 0; border-radius: 6px;
  border: 1px solid var(--c-border); background: transparent;
  color: var(--c-sub); font-weight: 600; transition: all .15s;
}
.class-btn:hover:not(:disabled) { border-color: var(--c-accent); color: var(--c-ink); }
.class-btn.active {
  background: var(--c-accent); border-color: var(--c-accent);
  color: #06121c;
}
.class-btn:disabled { opacity: .5; cursor: not-allowed; }

.side-foot { margin-top: auto; padding-top: 8px; }

/* ── 主区 ── */
.main { flex: 1; min-width: 0; display: flex; flex-direction: column; }

.tabs {
  display: flex; align-items: center; gap: 4px;
  padding: 0 20px; height: 54px;
  background: var(--c-surface);
  border-bottom: 1px solid var(--c-border);
  position: sticky; top: 0; z-index: 20;
}
.tab {
  display: flex; align-items: center; gap: 6px;
  padding: 8px 16px; border-radius: 7px;
  color: var(--c-sub); text-decoration: none; font-size: 14px;
  transition: all .15s; white-space: nowrap;
}
.tab:hover { color: var(--c-ink); background: var(--c-surface-2); }
.tab.active { color: var(--c-ink); background: var(--c-surface-2); box-shadow: inset 0 -2px 0 var(--c-accent); }
.tab-icon { font-size: 15px; }

.tabs-right { margin-left: auto; display: flex; gap: 8px; align-items: center; }
.pill {
  font-size: 11px; padding: 3px 9px; border-radius: 20px;
  background: var(--c-surface-2); border: 1px solid var(--c-border); color: var(--c-sub);
}
.pill.warn { color: var(--sev-mild); border-color: #6b4a10; }
.pill.muted { opacity: .7; }

.content { flex: 1; padding: 20px; min-width: 0; }
.connection-panel { border: 1px solid var(--c-border); background: var(--c-surface-2); padding: 20px; border-radius: 8px; margin-bottom: 16px; }
.connection-panel h2 { margin-top: 0; font-size: 18px; }
.connection-panel p { color: var(--c-sub); overflow-wrap: anywhere; }
.connection-panel button { padding: 8px 14px; color: var(--c-ink); background: var(--c-surface); border: 1px solid var(--c-accent); border-radius: 6px; }

.error-bar {
  background: #3a1620; border: 1px solid #7b2740; color: #ffb3c1;
  padding: 10px 14px; border-radius: 8px; margin-bottom: 16px; font-size: 13px;
}
</style>
