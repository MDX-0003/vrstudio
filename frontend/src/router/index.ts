import { createRouter, createWebHistory } from 'vue-router'
import CaseListView from '@/views/CaseListView.vue'
import WizardView from '@/views/WizardView.vue'
import TaskCenterView from '@/views/TaskCenterView.vue'
import SettingsView from '@/views/SettingsView.vue'

const router = createRouter({
  history: createWebHistory('/next/'),
  routes: [
    { path: '/', redirect: '/cases' },
    {
      path: '/cases',
      name: 'cases',
      component: CaseListView,
      meta: { title: 'Case 列表' },
    },
    {
      path: '/case/:name/:step?',
      name: 'case',
      component: WizardView,
      meta: { title: 'Case' },
    },
    {
      path: '/tasks',
      name: 'tasks',
      component: TaskCenterView,
      meta: { title: '任务中心' },
    },
    {
      path: '/settings',
      name: 'settings',
      component: SettingsView,
      meta: { title: '设置' },
    },
    { path: '/:pathMatch(.*)*', redirect: '/cases' },
  ],
})

export default router
