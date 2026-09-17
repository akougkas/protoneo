<template>
  <div class="review-view">
    <!-- Header -->
    <header class="app-header">
      <div class="header-left">
        <div class="brand" @click="goHome">PROTONEO</div>
        <span class="product-tag">{{ appDisplayName }}</span>
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
          :isSimulating="isGraphBuilding"
          @refresh="refreshGraph"
          @toggle-maximize="toggleMaximize('graph')"
        />
      </div>

      <!-- Right Panel: Review Workbench -->
      <div class="panel-wrapper right" :style="rightPanelStyle">
        <SessionPanel
          :key="sessionId"
          :session-id="sessionId"
          :conference="conference"
          @back="goHome"
          @graph-update="onGraphUpdate"
          @graph-step-view="onGraphStepView"
          @request-graph-focus="viewMode = 'split'"
          @stage-changed="onStageChanged"
          @status-changed="onStatusChanged"
        />
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref, computed, inject, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import GraphPanel from '../components/GraphPanel.vue'
import SessionPanel from '../components/SessionPanel.vue'
import { getSession, getSessionGraph, getGraphAtStep } from '../api/kernel.js'

const route = useRoute()
const router = useRouter()
const activeApp = inject('activeApp', ref(null))
const appDisplayName = computed(() => activeApp.value?.display_name || 'Paper Review')

const sessionId = computed(() => route.params.sessionId)
const conference = computed(() => route.query.conference || 'adaptive')

// Layout
const viewMode = ref(window.innerWidth < 760 ? 'review' : 'split')

// Session status tracking
const sessionStatus = ref('running')
const currentPhase = ref('')
const paperTitle = ref('')
const sessionMeta = ref({})

// Graph data generated from paper metadata
const paperGraphData = ref(null)
const graphLoading = ref(false)

// Polling
let statusPoll = null

const isReviewing = computed(() =>
  sessionStatus.value === 'running'
)

const isGraphBuilding = computed(() => isReviewing.value && !sessionMeta.value.skip_graph && currentPhase.value === 'pre_review')
const graphPhase = computed(() => isGraphBuilding.value ? 1 : 2)

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
  if (sessionStatus.value === 'stopped') return 'Stopped'
  if (
    sessionStatus.value === 'created'
    && sessionMeta.value?.pipeline_mode === 'imported_graph_review'
    && sessionMeta.value?.graph_source === 'imported'
  ) return 'Graph Ready'
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
  router.push({ name: 'Home' })
}

function onStatusChanged(value) {
  sessionStatus.value = value
  if (['completed', 'failed', 'stopped'].includes(value)) { stopPolling(); refreshGraph() }
  else if (!statusPoll) statusPoll = setInterval(pollSession, 5000)
}

function onStageChanged({ stage, step }) {
  currentPhase.value = stage
  // Auto-switch view: show graph during pre-review, show reviews during review
  if (stage === 'pre_review') {
    if (window.innerWidth >= 760 && !sessionMeta.value.skip_graph && viewMode.value === 'review') viewMode.value = 'split'
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
    if (res.data.current_stage) currentPhase.value = res.data.current_stage
    // Extract paper title from session config
    const cfg = res.data?.config || res.data?.metadata
    if (cfg) {
      const meta = cfg.metadata || cfg
      const firstLoad = !Object.keys(sessionMeta.value).length
      sessionMeta.value = meta || {}
      if (firstLoad && meta.skip_graph) viewMode.value = 'review'
      if (!paperTitle.value && meta.paper_title) paperTitle.value = meta.paper_title
    }
    if (status === 'completed' || status === 'failed' || status === 'stopped') {
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
    if (window.innerWidth >= 760 && !sessionMeta.value.skip_graph && viewMode.value === 'review') viewMode.value = 'split'
  } catch (e) {
    console.warn('Step graph not available:', e)
  } finally {
    graphLoading.value = false
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
  background: var(--pn-bg);
  overflow: hidden;
}

/* ── Header ── */
.app-header {
  height: 48px;
  border-bottom: 1px solid var(--pn-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 var(--pn-space-5);
  background: var(--pn-surface);
  z-index: 100;
  position: relative;
}

.header-left {
  display: flex;
  align-items: center;
  gap: var(--pn-space-3);
}

.header-center {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
}

.brand {
  font-family: var(--pn-mono);
  font-weight: 700;
  font-size: 13px;
  letter-spacing: 0.18em;
  cursor: pointer;
  color: var(--pn-text);
  transition: color var(--pn-duration) var(--pn-ease);
}
.brand:hover { color: var(--pn-accent); }

.product-tag {
  font-family: var(--pn-serif);
  font-size: 12px;
  font-weight: 500;
  font-style: italic;
  color: var(--pn-text-secondary);
}

.view-switcher {
  display: flex;
  border: 1px solid var(--pn-border);
  gap: 0;
}

.switch-btn {
  border: none;
  border-right: 1px solid var(--pn-border);
  background: transparent;
  padding: var(--pn-space-1) var(--pn-space-4);
  font-family: var(--pn-mono);
  font-size: 10px;
  font-weight: 600;
  color: var(--pn-text-muted);
  cursor: pointer;
  transition: all var(--pn-duration) var(--pn-ease);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.switch-btn:last-child { border-right: none; }

.switch-btn.active {
  background: var(--pn-text);
  color: var(--pn-bg);
}

.switch-btn:hover:not(.active) {
  background: var(--pn-bg);
  color: var(--pn-text);
}

.header-right {
  display: flex;
  align-items: center;
  gap: var(--pn-space-3);
}

.session-phase {
  display: flex;
  align-items: center;
  gap: var(--pn-space-2);
}

.phase-label {
  font-family: var(--pn-mono);
  font-size: 11px;
  font-weight: 600;
  color: var(--pn-text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.step-divider {
  width: 1px;
  height: 14px;
  background-color: var(--pn-border);
}

.status-indicator {
  display: flex;
  align-items: center;
  gap: var(--pn-space-2);
  font-family: var(--pn-mono);
  font-size: 10px;
  color: var(--pn-text-muted);
  font-weight: 500;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--pn-border-strong);
}

.status-indicator.processing .dot { background: var(--pn-warn); animation: pn-pulse 1.8s ease-in-out infinite; }
.status-indicator.completed .dot { background: var(--pn-ok); }
.status-indicator.error .dot { background: var(--pn-err); }

/* ── Content ── */
.content-area {
  flex: 1;
  display: flex;
  position: relative;
  overflow: hidden;
}

.panel-wrapper {
  height: 100%;
  overflow: hidden;
  transition: width 0.4s var(--pn-ease), opacity 0.3s var(--pn-ease), transform 0.3s var(--pn-ease);
  will-change: width, opacity, transform;
}

.panel-wrapper.left {
  border-right: 1px solid var(--pn-border);
}

.paper-title-header {
  font-family: var(--pn-serif);
  font-size: 12px;
  font-style: italic;
  color: var(--pn-text-secondary);
  max-width: 350px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin-left: var(--pn-space-2);
  padding-left: var(--pn-space-3);
  border-left: 1px solid var(--pn-border);
}
@media (max-width: 760px) {
  .app-header { height: auto; min-height: 48px; flex-wrap: wrap; gap: 10px; padding: 12px; }
  .header-left { min-width: 0; }
  .header-center { position: static; transform: none; order: 3; width: 100%; }
  .product-tag, .paper-title-header, .session-phase, .step-divider { display: none; }
  .view-switcher { width: fit-content; margin: 0 auto; }
}
</style>
