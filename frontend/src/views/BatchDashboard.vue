<template>
  <div class="batch-dashboard">
    <header class="batch-header">
      <div class="header-left">
        <router-link to="/" class="back-link">ProtoNeo</router-link>
        <h1 class="batch-title">Batch {{ batchId.slice(0, 8) }}</h1>
        <span :class="['status-badge', batch.status]">{{ batch.status }}</span>
      </div>
      <div class="header-right">
        <span class="batch-meta">
          {{ batch.conference?.toUpperCase() }}
          <span v-if="batch.created_at"> &middot; {{ formatDate(batch.created_at) }}</span>
        </span>
      </div>
    </header>

    <!-- Progress summary -->
    <div class="progress-bar-container">
      <div class="progress-bar">
        <div class="progress-fill" :style="{ width: progressPct + '%' }"></div>
      </div>
      <div class="progress-text">
        {{ batch.completed || 0 }} / {{ batch.total || 0 }} graphs complete
        <span v-if="batch.failed"> &middot; {{ batch.failed }} failed</span>
      </div>
    </div>

    <!-- Paper cards grid -->
    <div class="paper-grid">
      <div
        v-for="sess in sessions"
        :key="sess.session_id"
        :class="['paper-card', sess.status]"
      >
        <div class="pc-header">
          <span class="pc-id">{{ sess.session_id.slice(0, 8) }}</span>
          <span :class="['pc-status', sess.status]">{{ sess.status }}</span>
        </div>
        <div class="pc-title">{{ sess.paper_title || sess.filename || 'Untitled' }}</div>
        <div v-if="sess.node_count" class="pc-stats">
          <span>{{ sess.node_count }} nodes</span>
          <span>{{ sess.edge_count }} edges</span>
        </div>
        <div class="pc-actions">
          <button
            class="pc-btn view-btn"
            @click="openSession(sess.session_id)"
          >View Graph</button>
          <button
            class="pc-btn review-btn"
            :disabled="sess.status !== 'completed'"
            @click="startReview(sess.session_id)"
          >Launch Review</button>
          <button
            v-if="sess.status === 'completed'"
            class="pc-btn export-btn"
            @click="doExportGraph(sess.session_id)"
          >Export</button>
        </div>
      </div>
    </div>

    <p v-if="error" class="error-msg">{{ error }}</p>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { getBatch, launchReview, exportGraph } from '../api/kernel.js'

const props = defineProps({ batchId: String })
const router = useRouter()

const batch = ref({})
const sessions = ref([])
const error = ref('')
let pollTimer = null

const progressPct = computed(() => {
  const total = batch.value.total || 1
  return Math.round(((batch.value.completed || 0) / total) * 100)
})

async function fetchBatch() {
  try {
    const res = await getBatch(props.batchId)
    batch.value = res.data
    sessions.value = res.data.sessions || []
  } catch (e) {
    error.value = e.response?.data?.detail || e.message
  }
}

function openSession(sid) {
  router.push({ name: 'PanelReview', params: { sessionId: sid } })
}

async function startReview(sid) {
  try {
    await launchReview(sid)
    router.push({ name: 'PanelReview', params: { sessionId: sid } })
  } catch (e) {
    error.value = e.response?.data?.detail || e.message
  }
}

async function doExportGraph(sid) {
  try {
    const res = await exportGraph(sid)
    const url = URL.createObjectURL(res.data)
    const a = document.createElement('a')
    a.href = url
    a.download = `graph-${sid.slice(0, 8)}.json`
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    error.value = e.response?.data?.detail || e.message
  }
}

function formatDate(isoStr) {
  if (!isoStr) return ''
  const d = new Date(isoStr)
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) +
    ' ' + d.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit' })
}

onMounted(() => {
  fetchBatch()
  pollTimer = setInterval(() => {
    if (batch.value.status === 'running') fetchBatch()
  }, 5000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<style scoped>
.batch-dashboard {
  max-width: 1100px;
  margin: 0 auto;
  padding: 40px 24px;
}

.batch-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 28px;
}

.header-left {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.back-link {
  font-family: 'Space Grotesk', sans-serif;
  font-size: 20px;
  font-weight: 700;
  color: #333;
  text-decoration: none;
}

.batch-title {
  font-family: 'JetBrains Mono', monospace;
  font-size: 18px;
  font-weight: 600;
}

.status-badge {
  font-size: 11px;
  font-weight: 700;
  padding: 2px 10px;
  border-radius: 3px;
  text-transform: uppercase;
  letter-spacing: 0.3px;
}

.status-badge.completed { background: #e8f5e9; color: #2a2; }
.status-badge.running { background: #fff8e1; color: #a07000; }
.status-badge.partial { background: #fff3e0; color: #e65100; }
.status-badge.failed { background: #ffebee; color: #900; }

.batch-meta {
  font-size: 13px;
  color: #888;
}

.progress-bar-container {
  margin-bottom: 28px;
}

.progress-bar {
  height: 6px;
  background: #eee;
  border-radius: 3px;
  overflow: hidden;
  margin-bottom: 6px;
}

.progress-fill {
  height: 100%;
  background: #000;
  border-radius: 3px;
  transition: width 0.3s;
}

.progress-text {
  font-size: 12px;
  color: #888;
}

.paper-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 16px;
}

.paper-card {
  border: 1px solid #e0e0e0;
  border-radius: 6px;
  padding: 18px 20px;
  transition: border-color 0.15s;
}

.paper-card:hover { border-color: #999; }
.paper-card.completed { border-left: 3px solid #4a4; }
.paper-card.running { border-left: 3px solid #e8a500; }
.paper-card.failed { border-left: 3px solid #c44; }

.pc-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.pc-id {
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  color: #888;
}

.pc-status {
  font-size: 10px;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 2px;
  text-transform: uppercase;
}

.pc-status.completed { background: #e8f5e9; color: #2a2; }
.pc-status.running { background: #fff8e1; color: #a07000; }
.pc-status.failed { background: #ffebee; color: #900; }
.pc-status.created { background: #f0f0f0; color: #888; }

.pc-title {
  font-size: 14px;
  font-weight: 600;
  color: #111;
  margin-bottom: 10px;
  line-height: 1.4;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.pc-stats {
  display: flex;
  gap: 12px;
  font-size: 12px;
  color: #888;
  margin-bottom: 12px;
}

.pc-actions {
  display: flex;
  gap: 8px;
}

.pc-btn {
  font-size: 11px;
  font-weight: 600;
  padding: 5px 12px;
  border-radius: 4px;
  border: 1px solid #ddd;
  background: #fff;
  color: #333;
  cursor: pointer;
  transition: all 0.15s;
}

.pc-btn:hover { border-color: #000; color: #000; }
.pc-btn:disabled { opacity: 0.4; cursor: not-allowed; }

.review-btn { border-color: #000; background: #000; color: #fff; }
.review-btn:hover:not(:disabled) { background: #222; }

.export-btn { font-size: 10px; padding: 4px 8px; }

.error-msg {
  color: #c00;
  font-size: 13px;
  margin-top: 16px;
  text-align: center;
}
</style>
