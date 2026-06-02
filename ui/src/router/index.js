import { createRouter, createWebHistory } from 'vue-router'
import Home from '../views/Home.vue'
import SessionView from '../views/SessionView.vue'
import PostReviewView from '../views/PostReviewView.vue'
import BatchView from '../views/BatchView.vue'
import SettingsView from '../views/SettingsView.vue'

const routes = [
  {
    path: '/',
    name: 'Home',
    component: Home
  },
  {
    path: '/session/:sessionId',
    name: 'Session',
    component: SessionView,
    props: true
  },
  {
    path: '/session/:sessionId/post-review',
    name: 'PostReview',
    component: PostReviewView,
    props: true
  },
  {
    path: '/batch/:batchId',
    name: 'Batch',
    component: BatchView,
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
