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
        <span v-if="batch.status === 'running'" class="elapsed"> &middot; {{ elapsedStr }}</span>
      </div>
    </div>

    <!-- Paper cards -->
    <div class="paper-list">
      <div
        v-for="sess in sessions"
        :key="sess.session_id"
        :class="['paper-card', sess.status]"
      >
        <div class="pc-header">
          <div class="pc-header-left">
            <span class="pc-id">{{ sess.session_id.slice(0, 8) }}</span>
            <span class="pc-title">{{ sess.paper_title || sess.filename || 'Untitled' }}</span>
          </div>
          <span :class="['pc-status', sess.status]">{{ statusLabel(sess) }}</span>
        </div>

        <!-- Pipeline steps row -->
        <div class="pipeline-steps">
          <div
            v-for="step in pipelineStepDefs"
            :key="step.key"
            :class="['step-pip', stepStatus(sess, step.key)]"
            :title="step.label + ': ' + stepStatus(sess, step.key)"
          >
            <div class="step-dot"></div>
            <span class="step-label">{{ step.short }}</span>
          </div>
        </div>

        <!-- Stats row (when graph exists) -->
        <div v-if="sess.node_count" class="pc-stats">
          <span>{{ sess.node_count }} nodes</span>
          <span>{{ sess.edge_count }} edges</span>
        </div>

        <!-- Actions -->
        <div class="pc-actions">
          <button
            v-if="sess.status === 'running' || sess.status === 'created'"
            class="pc-btn stop-btn"
            @click="doCancel(sess.session_id)"
          >Stop</button>
          <button
            class="pc-btn view-btn"
            :disabled="sess.status === 'created'"
            @click="openSession(sess.session_id)"
          >View Graph</button>
          <button
            class="pc-btn review-btn"
            :disabled="sess.status !== 'completed'"
            @click="showReviewConfig(sess)"
          >Launch Review</button>
          <button
            v-if="sess.status === 'completed'"
            class="pc-btn export-btn"
            @click="doExportGraph(sess.session_id)"
          >Export</button>
        </div>
      </div>
    </div>

    <!-- Review config modal -->
    <div v-if="reviewModal" class="modal-overlay" @click.self="reviewModal = null">
      <div class="modal-panel">
        <h3>Launch Review</h3>
        <p class="modal-sub">{{ reviewModal.paper_title || reviewModal.filename }}</p>

        <label class="modal-label">Conference</label>
        <select v-model="reviewConference" class="modal-select">
          <option v-for="c in conferences" :key="c.slug" :value="c.slug">{{ c.name }}</option>
        </select>

        <label class="modal-label">Reviewer Models</label>
        <div v-for="role in reviewRoles" :key="role" class="model-row">
          <span class="role-label">{{ role }}</span>
          <select v-model="reviewModelMap[role]" class="modal-select model-select">
            <option value="">Default</option>
            <option v-for="m in models" :key="m.model_id" :value="m.model_id">{{ m.display_name || m.model_id }}</option>
          </select>
        </div>

        <label class="modal-label">Instructions (optional)</label>
        <textarea v-model="reviewInstructions" class="modal-textarea" rows="3" placeholder="Additional reviewer instructions..."></textarea>

        <div class="modal-actions">
          <button class="pc-btn" @click="reviewModal = null">Cancel</button>
          <button class="pc-btn review-btn" @click="doLaunchReview" :disabled="reviewLaunching">
            {{ reviewLaunching ? 'Launching...' : 'Start Review' }}
          </button>
        </div>
      </div>
    </div>

    <p v-if="error" class="error-msg">{{ error }}</p>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { getBatch, exportGraph, connectStream, getModels, getConferences, pipelineCancel } from '../api/kernel.js'
import kernel from '../api/kernel.js'

const props = defineProps({ batchId: String })
const router = useRouter()

const batch = ref({})
const sessions = ref([])
const error = ref('')
const models = ref([])
const conferences = ref([])
const startTime = ref(Date.now())
const elapsed = ref(0)

// Review config modal state
const reviewModal = ref(null)
const reviewConference = ref('')
const reviewModelMap = reactive({})
const reviewInstructions = ref('')
const reviewLaunching = ref(false)
const reviewRoles = ref([])

let pollTimer = null
let elapsedTimer = null
const wsConnections = {}

const pipelineStepDefs = [
  { key: 'parse', label: 'Parse', short: 'Parse' },
  { key: 'nlp_prepass', label: 'NLP Pre-pass', short: 'NLP' },
  { key: 'ontology', label: 'Ontology', short: 'Onto' },
  { key: 'extract', label: 'Extraction', short: 'Extract' },
  { key: 'coref', label: 'Co-reference', short: 'Coref' },
  { key: 'verify', label: 'Verification', short: 'Verify' },
  { key: 'summarize', label: 'Summarize', short: 'Sum' },
]

const progressPct = computed(() => {
  const total = batch.value.total || 1
  return Math.round(((batch.value.completed || 0) / total) * 100)
})

const elapsedStr = computed(() => {
  const s = Math.floor(elapsed.value / 1000)
  const m = Math.floor(s / 60)
  return m > 0 ? `${m}m ${s % 60}s` : `${s}s`
})

function statusLabel(sess) {
  if (sess.status === 'running') {
    const steps = sess.pipeline_steps || {}
    const running = Object.entries(steps).find(([, v]) => {
      const st = typeof v === 'string' ? v : (v?.status || '')
      return st === 'running'
    })
    if (running) return running[0]
  }
  return sess.status
}

function stepStatus(sess, stepKey) {
  const steps = sess.pipeline_steps || {}
  const step = steps[stepKey]
  if (!step) return 'pending'
  if (typeof step === 'string') return step
  return step.status || 'pending'
}

async function fetchBatch() {
  try {
    const res = await getBatch(props.batchId)
    batch.value = res.data
    // Merge pipeline_steps from poll into live session data
    const incoming = res.data.sessions || []
    for (const s of incoming) {
      const existing = sessions.value.find(e => e.session_id === s.session_id)
      if (existing) {
        Object.assign(existing, s)
      } else {
        sessions.value.push(s)
      }
    }
    // Connect WebSocket for any running session we haven't connected to yet
    for (const s of sessions.value) {
      if ((s.status === 'running' || s.status === 'created') && !wsConnections[s.session_id]) {
        connectWs(s.session_id)
      }
    }
  } catch (e) {
    error.value = e.response?.data?.detail || e.message
  }
}

function connectWs(sid) {
  try {
    const ws = connectStream(sid)
    wsConnections[sid] = ws
    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data)
        handleWsEvent(sid, msg)
      } catch { /* ignore non-JSON */ }
    }
    ws.onclose = () => { delete wsConnections[sid] }
    ws.onerror = () => { delete wsConnections[sid] }
  } catch { /* connectStream unavailable */ }
}

function handleWsEvent(sid, msg) {
  const sess = sessions.value.find(s => s.session_id === sid)
  if (!sess) return

  const type = msg.type || msg.event
  const data = msg.data || msg

  if (type === 'step_started') {
    const stepName = data.step
    if (!sess.pipeline_steps) sess.pipeline_steps = {}
    sess.pipeline_steps[stepName] = { status: 'running' }
    sess.status = 'running'
  }
  if (type === 'stage_complete' && data.stage === 'pre_review') {
    sess.status = 'completed'
  }
  if (type === 'graph_complete' || type === 'graph_updated') {
    if (data.node_count) sess.node_count = data.node_count
    if (data.edge_count) sess.edge_count = data.edge_count
  }
  if (type === 'metadata_extracted') {
    if (data.title) sess.paper_title = data.title
  }
  if (type === 'completed') {
    sess.status = 'completed'
    // Mark all steps complete
    for (const step of pipelineStepDefs) {
      if (sess.pipeline_steps?.[step.key]) {
        sess.pipeline_steps[step.key].status = 'complete'
      }
    }
    // Update batch counts
    fetchBatch()
  }
  if (type === 'error') {
    sess.status = 'failed'
    fetchBatch()
  }
  // Step completion: mark previous step complete when next one starts
  if (type === 'step_started' && sess.pipeline_steps) {
    const idx = pipelineStepDefs.findIndex(s => s.key === data.step)
    if (idx > 0) {
      const prevKey = pipelineStepDefs[idx - 1].key
      if (sess.pipeline_steps[prevKey]) {
        sess.pipeline_steps[prevKey].status = 'complete'
      }
    }
  }
}

function openSession(sid) {
  router.push({ name: 'PanelReview', params: { sessionId: sid } })
}

function showReviewConfig(sess) {
  reviewModal.value = sess
  reviewConference.value = batch.value.conference || 'hpdc26'
  // Extract reviewer roles from session config
  const agentKeys = Object.keys(sess.pipeline_steps || {}).length > 0
    ? ['technical', 'methodology', 'clarity', 'adversarial', 'meta']
    : ['technical', 'methodology', 'clarity', 'adversarial', 'meta']
  reviewRoles.value = agentKeys
  for (const role of agentKeys) {
    reviewModelMap[role] = ''
  }
  reviewInstructions.value = ''
}

async function doLaunchReview() {
  if (!reviewModal.value) return
  reviewLaunching.value = true
  try {
    const body = {
      model_map: { ...reviewModelMap },
      conference: reviewConference.value,
      user_instructions: reviewInstructions.value,
    }
    await kernel.post(`/api/sessions/${reviewModal.value.session_id}/launch-review`, body)
    router.push({ name: 'PanelReview', params: { sessionId: reviewModal.value.session_id } })
  } catch (e) {
    error.value = e.response?.data?.detail || e.message
  } finally {
    reviewLaunching.value = false
    reviewModal.value = null
  }
}

async function doCancel(sid) {
  try {
    await pipelineCancel(sid)
    const sess = sessions.value.find(s => s.session_id === sid)
    if (sess) sess.status = 'stopped'
    fetchBatch()
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

async function loadMeta() {
  try {
    const [mRes, cRes] = await Promise.all([getModels(), getConferences()])
    models.value = mRes.data.models || []
    conferences.value = cRes.data.conferences || []
  } catch { /* non-critical */ }
}

onMounted(() => {
  fetchBatch()
  loadMeta()
  pollTimer = setInterval(() => {
    if (batch.value.status === 'running' || batch.value.status === 'created') fetchBatch()
  }, 3000)
  elapsedTimer = setInterval(() => {
    elapsed.value = Date.now() - startTime.value
  }, 1000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
  if (elapsedTimer) clearInterval(elapsedTimer)
  for (const ws of Object.values(wsConnections)) {
    try { ws.close() } catch { /* ignore */ }
  }
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
.status-badge.stopped { background: #f5f5f5; color: #888; }
.status-badge.created { background: #e3f2fd; color: #1565c0; }

.batch-meta {
  font-size: 13px;
  color: #888;
}

.elapsed { font-family: 'JetBrains Mono', monospace; }

.progress-bar-container { margin-bottom: 28px; }
.progress-bar { height: 6px; background: #eee; border-radius: 3px; overflow: hidden; margin-bottom: 6px; }
.progress-fill { height: 100%; background: #000; border-radius: 3px; transition: width 0.3s; }
.progress-text { font-size: 12px; color: #888; }

/* Paper cards (full width, stacked) */
.paper-list { display: flex; flex-direction: column; gap: 12px; }

.paper-card {
  border: 1px solid #e0e0e0;
  border-radius: 6px;
  padding: 16px 20px;
  transition: border-color 0.15s;
}

.paper-card:hover { border-color: #999; }
.paper-card.completed { border-left: 3px solid #4a4; }
.paper-card.running { border-left: 3px solid #e8a500; }
.paper-card.failed { border-left: 3px solid #c44; }
.paper-card.stopped { border-left: 3px solid #bbb; }
.paper-card.created { border-left: 3px solid #90caf9; }

.pc-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}

.pc-header-left {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}

.pc-id {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  color: #aaa;
  flex-shrink: 0;
}

.pc-title {
  font-size: 14px;
  font-weight: 600;
  color: #111;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.pc-status {
  font-size: 10px;
  font-weight: 700;
  padding: 1px 8px;
  border-radius: 2px;
  text-transform: uppercase;
  white-space: nowrap;
  flex-shrink: 0;
}

.pc-status.completed { background: #e8f5e9; color: #2a2; }
.pc-status.running { background: #fff8e1; color: #a07000; }
.pc-status.failed { background: #ffebee; color: #900; }
.pc-status.created { background: #e3f2fd; color: #1565c0; }
.pc-status.stopped { background: #f5f5f5; color: #888; }

/* Pipeline steps visualization */
.pipeline-steps {
  display: flex;
  gap: 2px;
  margin-bottom: 10px;
  align-items: center;
}

.step-pip {
  display: flex;
  align-items: center;
  gap: 4px;
  flex: 1;
  min-width: 0;
}

.step-pip::after {
  content: '';
  flex: 1;
  height: 2px;
  background: #e0e0e0;
  min-width: 8px;
}

.step-pip:last-child::after { display: none; }

.step-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #e0e0e0;
  flex-shrink: 0;
  transition: background 0.3s;
}

.step-pip.complete .step-dot { background: #4a4; }
.step-pip.running .step-dot { background: #e8a500; animation: pulse 1s infinite; }
.step-pip.failed .step-dot { background: #c44; }

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

.step-label {
  font-size: 9px;
  font-weight: 600;
  color: #bbb;
  text-transform: uppercase;
  letter-spacing: 0.3px;
  white-space: nowrap;
}

.step-pip.complete .step-label { color: #4a4; }
.step-pip.running .step-label { color: #a07000; }

.pc-stats {
  display: flex;
  gap: 12px;
  font-size: 12px;
  color: #888;
  margin-bottom: 10px;
  font-family: 'JetBrains Mono', monospace;
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

.pc-btn:hover:not(:disabled) { border-color: #000; color: #000; }
.pc-btn:disabled { opacity: 0.4; cursor: not-allowed; }

.review-btn { border-color: #000; background: #000; color: #fff; }
.review-btn:hover:not(:disabled) { background: #222; }

.stop-btn { border-color: #c44; color: #c44; }
.stop-btn:hover { background: #c44; color: #fff; border-color: #c44; }

.export-btn { font-size: 10px; padding: 4px 8px; }

/* Modal */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}

.modal-panel {
  background: #fff;
  border-radius: 8px;
  padding: 28px 32px;
  max-width: 480px;
  width: 90%;
  max-height: 80vh;
  overflow-y: auto;
}

.modal-panel h3 {
  font-size: 16px;
  font-weight: 700;
  margin: 0 0 4px;
}

.modal-sub {
  font-size: 12px;
  color: #888;
  margin: 0 0 20px;
}

.modal-label {
  display: block;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #888;
  margin: 14px 0 6px;
}

.modal-select {
  width: 100%;
  padding: 6px 8px;
  border: 1px solid #ddd;
  border-radius: 4px;
  font-size: 13px;
  font-family: inherit;
}

.model-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}

.role-label {
  font-size: 12px;
  font-weight: 600;
  min-width: 100px;
  color: #555;
}

.model-select { flex: 1; }

.modal-textarea {
  width: 100%;
  padding: 8px;
  border: 1px solid #ddd;
  border-radius: 4px;
  font-size: 13px;
  font-family: inherit;
  resize: vertical;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 20px;
}

.error-msg {
  color: #c00;
  font-size: 13px;
  margin-top: 16px;
  text-align: center;
}
</style>
