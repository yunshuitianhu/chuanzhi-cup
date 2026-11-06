import { createRouter, createWebHistory } from 'vue-router'

// 路由划分与 app.py 的四个标签页一一对应，便于对照移植。
const routes = [
  { path: '/', name: 'welcome', component: () => import('./views/WelcomeView.vue') },
  { path:'/simple', name:'simple', component:()=>import('./views/SimpleView.vue'), meta:{mode:'simple',title:'简易版'} },
  {
    path: '/chat',
    name: 'chat',
    component: () => import('./views/ChatView.vue'),
    meta: { mode:'professional', title: '智能对话', icon: '💬' },
  },
  {
    path: '/charts',
    name: 'charts',
    component: () => import('./views/ChartsView.vue'),
    meta: { mode:'professional', title: '分析图表', icon: '📊' },
  },
  {
    path: '/river',
    name: 'river',
    component: () => import('./views/RiverView.vue'),
    meta: { mode:'professional', title: '河道模型', icon: '🌊' },
  },
  {
    path: '/tech',
    name: 'tech',
    component: () => import('./views/TechView.vue'),
    meta: { mode:'professional', title: '技术展示', icon: '🔬' },
  },
]

export default createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior(to, from, savedPosition) {
    if (savedPosition) return savedPosition
    if (to.hash) return { el: to.hash, top: 84 }
    if (to.path !== from.path || from.hash) return { top: 0 }
    return false
  },
})

export { routes }
