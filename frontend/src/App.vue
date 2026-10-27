<script setup>
/**
 * 应用外壳：侧边栏（配置 + 数据） + 顶部标签导航 + 路由视图。
 *
 * 结构对应 app.py：侧边栏 = st.sidebar，标签导航 = st.tabs 的四个页签。
 */
import { nextTick, onMounted, onBeforeUnmount, ref, shallowRef, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSessionStore } from './stores/session'
import { routes } from './router'
import StatusBadge from './components/StatusBadge.vue'
import AppIcon from './components/AppIcon.vue'
import DataPanel from './components/DataPanel.vue'
import ProjectPanel from './components/ProjectPanel.vue'
import TextSelectionMenu from './components/TextSelectionMenu.vue'
import SideQuestions from './components/SideQuestions.vue'
import TextSourceReturn from './components/TextSourceReturn.vue'
import Toast from './components/Toast.vue'
import { createRouteLoadRecovery } from './utils/routeFailures.js'

const store = useSessionStore()
const route = useRoute()
const router = useRouter()
const routeNotice = shallowRef(null)
const routeRecovery = createRouteLoadRecovery({
  isBusy: () => !!(store.chatRunning || store.loading || store.switchingProject),
  notify: notice => { routeNotice.value = notice },
})
const detachRecovery = routeRecovery.attach(router)
watch(() => [store.chatRunning, store.loading, store.switchingProject], () => routeRecovery.refreshBusy())
onBeforeUnmount(detachRecovery)
const navItems = routes.filter((r) => r.meta)
const narrow = ref(false), mobileOpen = ref(false), desktopCollapsed = ref(false)
const sidebar = ref(null), sidebarToggle = ref(null)
const sidebarOpen = computed(() => narrow.value ? mobileOpen.value : !desktopCollapsed.value)
const sidebarVisible=computed(()=>sidebarOpen.value&&!store.textQuestions.open)
const quoteModal=computed(()=>store.textQuestions.open&&rightNarrow.value)
const rightNarrow=ref(false)
let rightMedia
onMounted(()=>{rightMedia=matchMedia('(max-width:1279px)');rightNarrow.value=rightMedia.matches;rightMedia.onchange=e=>{rightNarrow.value=e.matches}})
onBeforeUnmount(()=>{if(rightMedia)rightMedia.onchange=null})
watch(()=>store.textQuestions.open,open=>{if(open)mobileOpen.value=false})
let media, previousOverflow = ''

function toggleSidebar() {
  if(store.textQuestions.open){store.textQuestions.open=false;if(narrow.value)mobileOpen.value=true;else desktopCollapsed.value=false;return}
  if (narrow.value) mobileOpen.value = !mobileOpen.value
  else {
    desktopCollapsed.value = !desktopCollapsed.value
    try { localStorage.setItem('wq:sidebar-collapsed', String(desktopCollapsed.value)) } catch { /* UI preference is optional. */ }
  }
}
async function closeSidebar() {
  mobileOpen.value = false
  await nextTick()
  sidebarToggle.value?.focus()
}
function sidebarKeys(event) {
  if (!narrow.value || !mobileOpen.value) return
  if (event.key === 'Escape') { event.preventDefault(); closeSidebar(); return }
  if (event.key !== 'Tab') return
  const controls = [...sidebar.value.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href],summary,[tabindex="0"]')]
    .filter(node => node.getClientRects().length)
  const first = controls[0], last = controls.at(-1)
  if ((event.shiftKey && document.activeElement === first) || (!event.shiftKey && document.activeElement === last)) {
    event.preventDefault(); (event.shiftKey ? last : first)?.focus()
  }
}
function syncViewport() { narrow.value = media.matches; mobileOpen.value = false }
watch(() => narrow.value && mobileOpen.value, async open => {
  document.body.style.overflow = open ? 'hidden' : previousOverflow
  if (open) { await nextTick(); sidebar.value?.querySelector('button')?.focus() }
})
watch(() => route.path, () => { if (narrow.value && mobileOpen.value) closeSidebar() })
onMounted(() => {
  previousOverflow = document.body.style.overflow
  media = window.matchMedia('(max-width:1023px)')
  syncViewport(); media.addEventListener('change', syncViewport)
  try { desktopCollapsed.value = localStorage.getItem('wq:sidebar-collapsed') === 'true' } catch { /* optional */ }
})
onBeforeUnmount(() => { media?.removeEventListener('change',syncViewport); document.body.style.overflow=previousOverflow })

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
  <div class="shell" :class="{'has-side-questions':store.textQuestions.open}">
    <a class="skip-link" href="#main-content">跳到主要内容</a>
    <button v-if="narrow && mobileOpen" class="sidebar-backdrop" tabindex="-1" aria-label="关闭项目侧栏" @click="closeSidebar"></button>
    <!-- ─────────── 侧边栏 ─────────── -->
    <aside v-show="sidebarOpen&&!store.textQuestions.open" id="project-sidebar" ref="sidebar" class="sidebar" :class="{drawer:narrow}" :role="narrow ? 'dialog' : undefined" :aria-modal="narrow ? 'true' : undefined" aria-label="项目与数据" @keydown="sidebarKeys">
      <button v-if="narrow" class="drawer-close" @click="closeSidebar">关闭项目侧栏</button>
      <div class="brand"><div class="brand-symbol"><AppIcon name="river" /></div><div><div class="brand-title">水质监测与污染溯源助手</div><div class="brand-sub">河道巡检 · 证据辅助研判</div></div></div>

      <ProjectPanel />

      <DataPanel :key="store.workspaceRevision" />

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

      <details class="service-settings"><summary>外部服务配置<span>{{ keySummary.filter(k=>k.ok).length }}/3 已配置</span></summary><div class="service-body"><h3 class="panel-title">密钥状态</h3>
        <div class="key-row" v-for="k in keySummary" :key="k.name">
          <span class="dot" :class="{ ok: k.ok }"></span>
          <span class="key-name">{{ k.name }}</span>
          <span class="key-tag">{{ k.ok ? '已配置' : (k.required ? '必填' : '可选') }}</span>
        </div>
        <p class="hint">
          密钥通过后端 <code>.env</code> 配置，前端不接触、不存储密钥。
        </p>
      </div></details>

      <footer class="side-foot">
        <StatusBadge :ok="store.ready" :text="store.ready ? '后端已连接' : '后端未连接'" />
      </footer>
    </aside>

    <!-- ─────────── 主区 ─────────── -->
    <main class="main" :inert="(narrow && mobileOpen)||quoteModal">
      <nav class="tabs" aria-label="主要导航">
        <button ref="sidebarToggle" class="sidebar-toggle" :aria-expanded="sidebarVisible" aria-controls="project-sidebar" :aria-label="sidebarVisible ? '收起项目侧栏' : '展开项目侧栏'" :title="sidebarVisible ? '收起项目侧栏' : '展开项目侧栏'" @click="toggleSidebar"><AppIcon name="panel" /><span class="toggle-label">项目与数据</span></button>
        <div class="tab-links">
        <RouterLink
          v-for="item in navItems"
          :key="item.path"
          :to="item.path"
          class="tab"
          :class="{ active: route.path === item.path }"
        >
          <AppIcon class="tab-icon" :name="item.name" />
          <span>{{ item.meta.title }}</span>
        </RouterLink>
        </div>

        <SideQuestions />
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

      <div id="main-content" class="content" tabindex="-1">
        <section v-if="routeNotice" class="route-notice" role="alert" aria-label="页面加载提示">
          <strong>{{ routeNotice.title || '页面加载' }}</strong>
          <p>{{ routeNotice.message }}</p>
          <p>{{ routeNotice.warning }}</p>
          <p v-if="routeNotice.blockedReason">{{ routeNotice.blockedReason }}</p>
          <div class="route-actions">
            <button :disabled="!routeNotice.canReload" @click="routeRecovery.retry()">{{ routeNotice.reloadLabel }}</button>
            <button @click="routeRecovery.dismiss()">关闭提示</button>
          </div>
        </section>
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
        <TextSourceReturn />
        <RouterView v-if="store.ready && !store.switchingProject" :key="store.workspaceRevision" />
      </div>
    </main>

    <TextSelectionMenu />
    <Toast />
  </div>
</template>

<style scoped>
.route-notice{border:1px solid var(--c-warning,#c99f54);background:var(--c-surface);border-radius:8px;padding:16px;margin-bottom:18px;overflow-wrap:anywhere}
.route-notice p{font-size:13px;line-height:1.7;margin:8px 0;color:var(--c-sub)}
.route-actions{display:flex;flex-wrap:wrap;gap:10px;margin-top:12px}
.route-actions button{padding:8px 12px;min-height:38px;border:1px solid var(--c-border);border-radius:6px;background:var(--c-surface-2);color:var(--c-ink)}
.route-actions button:disabled{opacity:.5}


.shell{display:flex;min-height:100vh}
.sidebar{width:280px;flex-shrink:0;background:#101d28;border-right:1px solid var(--c-border-soft);padding:26px 20px;display:flex;flex-direction:column;gap:24px;position:sticky;top:0;height:100dvh;overflow-y:auto;scrollbar-gutter:stable}
.brand{display:flex;align-items:center;gap:11px;padding-bottom:24px;border-bottom:1px solid var(--c-border-soft)}
.brand-symbol{width:34px;height:38px;flex-shrink:0;color:var(--c-accent);display:grid;place-items:center}
.brand-symbol svg{width:30px;height:30px}
.brand-title{font-size:14px;font-weight:650;line-height:1.65;letter-spacing:.02em}
.brand-sub{font-size:10px;color:var(--c-muted);margin-top:5px;letter-spacing:.06em}
.panel{padding:0 0 20px;border-bottom:1px solid var(--c-border-soft)}
.panel-title{margin:0 0 12px;font-size:12px;font-weight:500;color:var(--c-sub);letter-spacing:.06em}
.key-row{display:flex;align-items:center;gap:9px;padding:5px 0;font-size:12px}
.dot{width:6px;height:6px;border-radius:50%;background:var(--c-muted);flex-shrink:0}
.dot.ok{background:var(--c-accent-2)}
.key-name{flex:1}.key-tag{font-size:11px;color:var(--c-sub)}
.hint{margin:12px 0 0;font-size:11px;color:var(--c-sub);line-height:1.8}
.hint code{background:var(--c-bg);padding:1px 4px;border-radius:3px}
.service-settings{font-size:12px;color:var(--c-sub)}
.service-settings summary{display:flex;align-items:center;justify-content:space-between;gap:8px;list-style:none;min-height:36px}
.service-settings summary::before{content:'＋';color:var(--c-muted)}
.service-settings[open] summary::before{content:'−'}
.service-settings summary span{margin-left:auto;color:var(--c-muted);font-size:10px}
.service-body{padding-top:16px}.service-body .panel-title{display:none}
.class-picker{display:flex;gap:6px}
.class-btn{flex:1;padding:7px 0;border-radius:6px;border:1px solid var(--c-border);background:transparent;color:var(--c-sub);font-size:13px;font-weight:500;min-height:36px;transition:background var(--duration-fast),border-color var(--duration-fast)}
.class-btn:hover:not(:disabled){border-color:var(--c-accent);color:var(--c-ink)}
.class-btn.active{background:#263c4c;border-color:#9db6c5;color:var(--c-ink);box-shadow:inset 0 -2px 0 #9db6c5}
.class-btn:disabled{opacity:.5}
.side-foot{margin-top:auto;padding-top:8px}
.main{flex:1;min-width:0;display:flex;flex-direction:column}
.tabs{display:flex;align-items:center;gap:10px;padding:10px 24px;min-height:66px;background:var(--c-bg);border-bottom:1px solid var(--c-border-soft);position:sticky;top:0;z-index:20}
.tab-links{display:flex;min-width:0;overflow-x:auto;gap:4px;scrollbar-width:none}
.sidebar-toggle,.drawer-close{flex-shrink:0;display:inline-flex;align-items:center;justify-content:center;gap:7px;border:1px solid var(--c-border);border-radius:8px;padding:8px 10px;background:transparent;color:var(--c-sub);min-height:38px}
.sidebar-toggle svg{width:17px;height:17px}.toggle-label{font-size:11px}
.sidebar-toggle:hover{background:var(--c-surface);color:var(--c-ink)}
.sidebar-backdrop{position:fixed;inset:0;background:#0009;border:0;z-index:900}
.skip-link{position:fixed;left:12px;top:-100px;z-index:1200;padding:8px 12px;background:var(--c-accent);color:#06121c}
.skip-link:focus{top:8px}
.tab{display:flex;align-items:center;gap:8px;padding:9px 14px;border-radius:8px;color:var(--c-sub);text-decoration:none;font-size:13px;white-space:nowrap;}
.tab:hover{color:var(--c-ink);background:var(--c-surface)}
.tab.active{color:var(--c-accent);background:var(--c-accent-soft);box-shadow:inset 0 -2px 0 var(--c-accent);font-weight:600}
.tab-icon{width:17px;height:17px;flex-shrink:0}
.tabs-right{margin-left:auto;display:flex;gap:12px;align-items:center}
.pill{font-size:11px;color:var(--c-sub);white-space:nowrap;font-variant-numeric:tabular-nums}
.pill.warn{color:var(--sev-mild)}.pill.muted{color:var(--c-muted)}
.content{flex:1;padding:28px 30px;min-width:0;width:100%;max-width:1600px;margin:0 auto}
.connection-panel{border:1px solid var(--c-border);background:var(--c-surface);padding:28px;border-radius:var(--radius-md);margin-bottom:20px}
.connection-panel h2{margin-top:0;font-size:20px}.connection-panel p{color:var(--c-sub);overflow-wrap:anywhere}
.connection-panel button{padding:9px 16px;min-height:40px;color:#07222f;background:var(--c-accent);border:0;border-radius:var(--radius-sm)}
.error-bar{background:#301f29;border:1px solid #794354;color:#ffbdc7;padding:12px 16px;border-radius:var(--radius-sm);margin-bottom:20px;font-size:13px}
@media(min-width:1280px){.has-side-questions .main{margin-right:400px}.has-side-questions .tabs-right{display:none}.has-side-questions .content{padding:24px}}
@media(max-width:1450px){.tabs-right{display:none}.toggle-label{display:none}.tabs{gap:8px}.tab{padding:9px 12px}}
@media(max-width:1023px){
 .sidebar.drawer{position:fixed;inset:0 auto 0 0;z-index:1000;width:min(320px,calc(100vw - 24px));height:100dvh;overscroll-behavior:contain;padding:18px 20px}
 .drawer-close{align-self:flex-end}.tabs{padding:8px 12px;gap:8px;min-height:60px}.tab-links{flex:1;justify-content:space-evenly}
 .tab{font-size:12px;padding:8px 10px}.content{padding:22px 18px}.brand{padding-bottom:20px}
}
@media(max-width:600px){
 .tabs{gap:5px;padding:8px}.tab-links{justify-content:flex-start;gap:2px}.tab-links{gap:0;justify-content:space-between}.tab{font-size:11px;padding:8px 2px}.tab-icon{display:none}.sidebar-toggle{padding:8px}
 .tabs :deep(.side-launch){font-size:11px;padding:8px 6px}.content{padding:18px 10px}.connection-panel{padding:18px}.sidebar{gap:22px}
}

</style>
