<template>
  <div class="review-view">
    <!-- Header -->
    <header class="app-header">
      <div class="header-left">
        <div class="brand" @click="goHome">PROTONEO</div>
        <span class="product-tag">PC Panel</span>
        <span v-if="paperTitle" class="paper-title-header" :title="paperTitle">{{ paperTitle.length > 50 ? paperTitle.slice(0, 50) + '...' : paperTitle }}</span>
      </div>

      <div class="header-center">
        <div class="view-switcher">
          <button
            v-for="mode in ['graph', 'split', 'review']"
            :key="mode"
            class="switch-btn"
            :class="{ active: viewMode === mode }"
            @click="viewMode = mode"
          >
            {{ { graph: 'Graph', split: 'Split', review: 'Review' }[mode] }}
          </button>
        </div>
      </div>

      <div class="header-right">
        <div class="session-phase">
          <span class="phase-label">{{ phaseLabel }}</span>
        </div>
        <div class="step-divider"></div>
        <span class="status-indicator" :class="statusClass">
          <span class="dot"></span>
          {{ statusText }}
        </span>
      </div>
    </header>

    <!-- Main Content Area -->
    <main class="content-area">
      <!-- Left Panel: Paper Knowledge Graph -->
      <div class="panel-wrapper left" :style="leftPanelStyle">
        <GraphPanel
          :graphData="paperGraphData"
          :loading="graphLoading"
          :currentPhase="graphPhase"
          :isSimulating="isReviewing"
          @refresh="refreshGraph"
          @toggle-maximize="toggleMaximize('graph')"
        />
      </div>

      <!-- Right Panel: Review Workbench -->
      <div class="panel-wrapper right" :style="rightPanelStyle">
        <SessionPanel
          :session-id="sessionId"
          :conference="conference"
          @back="goHome"
          @graph-update="onGraphUpdate"
          @graph-step-view="onGraphStepView"
          @request-graph-focus="viewMode = 'split'"
          @stage-changed="onStageChanged"
        />
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import GraphPanel from '../components/GraphPanel.vue'
import SessionPanel from '../components/SessionPanel.vue'
import { getSession, getSessionGraph, extractGraph, getGraphAtStep } from '../api/kernel.js'

const route = useRoute()
const router = useRouter()

const sessionId = computed(() => route.params.sessionId)
const conference = computed(() => route.query.conference || 'hpdc26')

// Layout
const viewMode = ref('split')

// Session status tracking
const sessionStatus = ref('running')
const currentPhase = ref('')
const paperTitle = ref('')

// Graph data generated from paper metadata
const paperGraphData = ref(null)
const graphLoading = ref(false)

// Polling
let statusPoll = null

const isReviewing = computed(() =>
  sessionStatus.value === 'running' || sessionStatus.value === 'created'
)

const graphPhase = computed(() => isReviewing.value ? 3 : 2)

const phaseLabel = computed(() => {
  const labels = {
    pre_review: 'Pre-Review',
    review: 'Review',
    post_review: 'Post-Review',
  }
  return labels[currentPhase.value] || currentPhase.value || 'Session'
})

const statusClass = computed(() => {
  if (sessionStatus.value === 'completed') return 'completed'
  if (sessionStatus.value === 'failed') return 'error'
  return 'processing'
})

const statusText = computed(() => {
  if (sessionStatus.value === 'completed') return 'Complete'
  if (sessionStatus.value === 'failed') return 'Failed'
  return 'Reviewing'
})

// Layout computed styles (same pattern as MiroFish MainView)
const leftPanelStyle = computed(() => {
  if (viewMode.value === 'graph') return { width: '100%', opacity: 1, transform: 'translateX(0)' }
  if (viewMode.value === 'review') return { width: '0%', opacity: 0, transform: 'translateX(-20px)' }
  return { width: '50%', opacity: 1, transform: 'translateX(0)' }
})

const rightPanelStyle = computed(() => {
  if (viewMode.value === 'review') return { width: '100%', opacity: 1, transform: 'translateX(0)' }
  if (viewMode.value === 'graph') return { width: '0%', opacity: 0, transform: 'translateX(20px)' }
  return { width: '50%', opacity: 1, transform: 'translateX(0)' }
})

const toggleMaximize = (target) => {
  if (viewMode.value === target) {
    viewMode.value = 'split'
  } else {
    viewMode.value = target
  }
}

function goHome() {
  router.push({ name: 'Panel' })
}

function onStageChanged({ stage, step }) {
  currentPhase.value = stage
  // Auto-switch view: show graph during pre-review, show reviews during review
  if (stage === 'pre_review') {
    if (viewMode.value === 'review') viewMode.value = 'split'
  } else if (stage === 'review') {
    if (viewMode.value === 'graph') viewMode.value = 'split'
  }
}

/**
 * Build a graph visualization from paper metadata stored in the session.
 *
 * Transforms title, sections, and figure/table counts into GraphPanel's
 * expected node/edge format. This provides a structural overview until
 * Task 4 adds LLM-based knowledge graph extraction.
 */
function buildPaperGraph(metadata) {
  // Minimal structural graph for initial display before server graph loads
  if (!metadata) return null
  const nodes = [{ uuid: 'paper-root', name: metadata.title || 'Paper', labels: ['Entity', 'Paper'], attributes: {} }]
  return { nodes, edges: [] }
}

async function pollSession() {
  try {
    const res = await getSession(sessionId.value)
    const status = res.data?.status
    if (status) sessionStatus.value = status
    // Extract paper title from session config
    const cfg = res.data?.config || res.data?.metadata
    if (!paperTitle.value && cfg) {
      const meta = cfg.metadata || cfg
      if (meta.paper_title) paperTitle.value = meta.paper_title
    }
    if (status === 'completed' || status === 'failed') {
      stopPolling()
    }
  } catch {
    // Ignore transient errors
  }
}

function stopPolling() {
  if (statusPoll) {
    clearInterval(statusPoll)
    statusPoll = null
  }
}

function onGraphUpdate(data) {
  // Real-time graph update from chunk-by-chunk extraction
  if (data && data.nodes && data.nodes.length > 0) {
    paperGraphData.value = { nodes: data.nodes, edges: data.edges || [] }
  }
}

async function refreshGraph() {
  graphLoading.value = true
  try {
    const res = await getSessionGraph(sessionId.value)
    if (res.data && res.data.nodes?.length > 0) {
      paperGraphData.value = res.data
    }
  } catch {
    // Graph not available yet, keep existing
  } finally {
    graphLoading.value = false
  }
}

async function onGraphStepView(stepName) {
  graphLoading.value = true
  try {
    const res = await getGraphAtStep(sessionId.value, stepName)
    if (res.data && res.data.nodes?.length > 0) {
      paperGraphData.value = res.data
    }
    if (viewMode.value === 'review') viewMode.value = 'split'
  } catch (e) {
    console.warn('Step graph not available:', e)
  } finally {
    graphLoading.value = false
  }
}

async function tryExtractGraph() {
  try {
    await extractGraph(sessionId.value)
    await refreshGraph()
  } catch {
    // Extraction failed (no LLM available, etc.), use metadata graph
  }
}

onMounted(() => {
  // Build initial graph from metadata passed via query
  const metaJson = route.query.metadata
  if (metaJson) {
    try {
      const metadata = JSON.parse(decodeURIComponent(metaJson))
      paperTitle.value = metadata.title || ''
      paperGraphData.value = buildPaperGraph(metadata)
    } catch {
      // No metadata available, graph stays empty
    }
  }

  // Try to fetch server-side graph (may have richer data)
  refreshGraph()

  // Poll session status
  pollSession()
  statusPoll = setInterval(pollSession, 5000)
})

onUnmounted(() => {
  stopPolling()
})
</script>

<style scoped>
.review-view {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: #FFF;
  overflow: hidden;
  font-family: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif;
}

/* Header */
.app-header {
  height: 56px;
  border-bottom: 1px solid #EAEAEA;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  background: #FFF;
  z-index: 100;
  position: relative;
}

.header-left {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.header-center {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
}

.brand {
  font-family: 'JetBrains Mono', monospace;
  font-weight: 800;
  font-size: 18px;
  letter-spacing: 1px;
  cursor: pointer;
}

.product-tag {
  font-size: 11px;
  font-weight: 600;
  background: #000;
  color: #fff;
  padding: 2px 8px;
  border-radius: 3px;
  letter-spacing: 0.5px;
}

.view-switcher {
  display: flex;
  background: #F5F5F5;
  padding: 3px;
  border-radius: 6px;
  gap: 3px;
}

.switch-btn {
  border: none;
  background: transparent;
  padding: 5px 14px;
  font-size: 12px;
  font-weight: 600;
  color: #666;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.2s;
}

.switch-btn.active {
  background: #FFF;
  color: #000;
  box-shadow: 0 2px 4px rgba(0,0,0,0.05);
}

.header-right {
  display: flex;
  align-items: center;
  gap: 14px;
}

.session-phase {
  display: flex;
  align-items: center;
  gap: 8px;
}

.phase-label {
  font-size: 13px;
  font-weight: 600;
  color: #333;
}

.step-divider {
  width: 1px;
  height: 14px;
  background-color: #E0E0E0;
}

.status-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #666;
  font-weight: 500;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #CCC;
}

.status-indicator.processing .dot { background: #FF5722; animation: pulse 1s infinite; }
.status-indicator.completed .dot { background: #4CAF50; }
.status-indicator.error .dot { background: #F44336; }

@keyframes pulse { 50% { opacity: 0.5; } }

/* Content */
.content-area {
  flex: 1;
  display: flex;
  position: relative;
  overflow: hidden;
}

.panel-wrapper {
  height: 100%;
  overflow: hidden;
  transition: width 0.4s cubic-bezier(0.25, 0.8, 0.25, 1), opacity 0.3s ease, transform 0.3s ease;
  will-change: width, opacity, transform;
}

.panel-wrapper.left {
  border-right: 1px solid #EAEAEA;
}

.paper-title-header {
  font-size: 12px;
  color: #666;
  max-width: 350px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin-left: 8px;
  padding-left: 8px;
  border-left: 1px solid #ddd;
}
</style>
