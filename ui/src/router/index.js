import { createRouter, createWebHistory } from 'vue-router'
import PanelHome from '../views/PanelHome.vue'
import PanelReviewView from '../views/PanelReviewView.vue'
import BatchDashboard from '../views/BatchDashboard.vue'
import SettingsView from '../views/SettingsView.vue'

const routes = [
  {
    path: '/',
    name: 'Panel',
    component: PanelHome
  },
  {
    path: '/review/:sessionId',
    name: 'PanelReview',
    component: PanelReviewView,
    props: true
  },
  {
    path: '/batch/:batchId',
    name: 'BatchDashboard',
    component: BatchDashboard,
    props: true
  },
  {
    path: '/settings',
    name: 'Settings',
    component: SettingsView
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

export default router
