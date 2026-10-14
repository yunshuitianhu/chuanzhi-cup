import { createRouter, createWebHistory } from 'vue-router'

// 路由划分与 app.py 的四个标签页一一对应，便于对照移植。
const routes = [
  { path: '/', redirect: '/chat' },
  {
    path: '/chat',
    name: 'chat',
    component: () => import('./views/ChatView.vue'),
    meta: { title: '智能对话', icon: '💬' },
  },
  {
    path: '/charts',
    name: 'charts',
    component: () => import('./views/ChartsView.vue'),
    meta: { title: '分析图表', icon: '📊' },
  },
  {
    path: '/river',
    name: 'river',
    component: () => import('./views/RiverView.vue'),
    meta: { title: '河道模型', icon: '🌊' },
  },
  {
    path: '/tech',
    name: 'tech',
    component: () => import('./views/TechView.vue'),
    meta: { title: '技术展示', icon: '🔬' },
  },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})

export { routes }
