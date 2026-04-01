<template>
  <div class="session-panel">
    <!-- Back button -->
    <button class="back-btn" @click="$emit('back')">&larr; New Review</button>

    <!-- Stage Progress -->
    <div class="stage-bar">
      <div
        v-for="(stage, i) in stages"
        :key="stage.key"
        :class="['stage-block', stageClass(stage.key)]"
      >
        <div class="stage-header">
          <div class="stage-num">{{ i + 1 }}</div>
          <div class="stage-label">{{ stage.label }}</div>
        </div>
        <div v-if="stage.key === currentStage" class="sub-steps">
          <span
            v-for="step in stage.steps"
            :key="step.key"
            :class="['sub-step', stepClass(step.key)]"
          >
            {{ step.label }}
            <span v-if="step.key === 'deliberation' && deliberationRound.current > 0" class="sub-step-round">
              R{{ deliberationRound.current }}{{ deliberationRound.total ? '/' + deliberationRound.total : '' }}
            </span>
            <span v-if="pipelineSteps[step.key]?.model" class="sub-step-model">{{ shortModel(pipelineSteps[step.key].model) }}</span>
            <span v-if="pipelineSteps[step.key]?.duration" class="sub-step-dur">{{ Math.round(pipelineSteps[step.key].duration) }}s</span>
          </span>
        </div>
      </div>
    </div>

    <!-- Status Line -->
    <div class="status-line">
      <span :class="['status-dot', statusColor]"></span>
      <span class="status-text">{{ statusText }}</span>
      <span v-if="duration" class="duration">{{ duration }}</span>
    </div>

    <!-- Pre-Review Gate (between pre_review and review) -->
    <div v-if="showGate" class="pre-review-gate">
      <h3 class="gate-header">Pre-Review Complete</h3>
      <p class="gate-summary">
        Knowledge graph built: {{ gateStats.nodes }} entities, {{ gateStats.edges }} relationships.
        <span v-if="gateOntology?.paper_domain">Domain: <strong>{{ gateOntology.paper_domain }}</strong></span>
      </p>

      <!-- Graph Stats -->
      <div v-if="gateGraphStats" class="gate-stats-grid">
        <div class="gate-stat">
          <span class="gate-stat-val">{{ gateGraphStats.semantic_entities }}</span>
          <span class="gate-stat-lbl">entities</span>
        </div>
        <div class="gate-stat">
          <span class="gate-stat-val">{{ gateGraphStats.semantic_edges }}</span>
          <span class="gate-stat-lbl">relationships</span>
        </div>
        <div class="gate-stat">
          <span class="gate-stat-val">{{ Math.round(gateGraphStats.connectivity_ratio * 100) }}%</span>
          <span class="gate-stat-lbl">connected</span>
        </div>
        <div class="gate-stat">
          <span class="gate-stat-val">{{ gateGraphStats.sections_covered }}/{{ gateGraphStats.total_sections }}</span>
          <span class="gate-stat-lbl">sections</span>
        </div>
      </div>

      <!-- Reviewer Summary Preview -->
      <div v-if="gateReviewerSummary" class="gate-summary-preview">
        <div class="summary-preview-header" @click="showSummaryPreview = !showSummaryPreview">
          Reviewer Summary Preview
          <span class="toggle">{{ showSummaryPreview ? '−' : '+' }}</span>
        </div>
        <pre v-if="showSummaryPreview" class="summary-preview-body">{{ gateReviewerSummary }}</pre>
      </div>

      <div v-if="gateOntology?.key_contributions?.length" class="gate-contribs">
        <div class="contrib-label">Key Contributions:</div>
        <ul>
          <li v-for="(c, ci) in gateOntology.key_contributions" :key="ci">{{ c }}</li>
        </ul>
      </div>

      <div v-if="gateOntology" class="ontology-types">
        <div class="type-column">
          <div class="type-col-header">Entity Types ({{ gateOntology.entity_types?.length || 0 }})</div>
          <div v-for="et in gateOntology.entity_types" :key="et.name" class="type-chip entity">
            <strong>{{ et.name }}</strong>
            <span class="type-desc">{{ et.description }}</span>
          </div>
        </div>
        <div class="type-column">
          <div class="type-col-header">Relationship Types ({{ gateOntology.edge_types?.length || 0 }})</div>
          <div v-for="rt in gateOntology.edge_types" :key="rt.name" class="type-chip edge">
            <strong>{{ rt.name }}</strong>
            <span class="type-desc">{{ rt.description }}</span>
          </div>
        </div>
      </div>

      <div class="gate-actions">
        <button class="gate-btn view-graph" @click="emit('request-graph-focus')">View Graph</button>
        <button class="gate-btn export-graph" @click="doExportGraph">Export Graph</button>
        <button class="gate-btn proceed" @click="advancePipeline">Proceed to Review</button>
      </div>
    </div>

    <!-- Pipeline Step Cards -->
    <div v-if="Object.keys(pipelineSteps).length > 0" class="step-cards">
      <div
        v-for="step in allStepCards"
        :key="step.key"
        :class="['step-card', stepCardClass(step.key)]"
        @click="expandedStep = expandedStep === step.key ? '' : step.key"
      >
        <div class="step-card-header">
          <span :class="['step-icon', stepIconClass(step.key)]">{{ stepIcon(step.key) }}</span>
          <span class="step-card-label">{{ step.label }}</span>
          <span v-if="pipelineSteps[step.key]?.model" class="step-model">{{ pipelineSteps[step.key].model }}</span>
          <span v-if="pipelineSteps[step.key]?.duration" class="step-duration">{{ formatStepDuration(step.key) }}</span>
          <span v-if="isStaleStep(step.key)" class="stale-badge">outdated</span>
          <span class="step-expand">{{ expandedStep === step.key ? '−' : '+' }}</span>
        </div>
        <div v-if="expandedStep === step.key" class="step-card-details">
          <div class="step-detail-row" v-if="pipelineSteps[step.key]?.nodesAdded">
            Nodes: +{{ pipelineSteps[step.key].nodesAdded }}
          </div>
          <div class="step-detail-row" v-if="pipelineSteps[step.key]?.edgesAdded">
            Edges: +{{ pipelineSteps[step.key].edgesAdded }}
          </div>
          <div class="step-card-actions" v-if="pipelineSteps[step.key]?.status === 'complete'">
            <button class="step-action-btn" @click.stop="emit('graph-step-view', step.key)">View Graph</button>
            <button class="step-action-btn" @click.stop="rerunStep(step.key)">Re-run</button>
          </div>
        </div>
      </div>
    </div>

    <!-- Live Activity Ticker -->
    <div v-if="status === 'running' && events.length" class="activity-ticker">
      <div class="ticker-latest">
        <span class="ticker-dot"></span>
        {{ events[events.length - 1]?.text }}
      </div>
      <div class="ticker-context">
        <span v-if="currentStepDesc" class="ticker-desc">{{ currentStepDesc }}</span>
        <span v-if="currentStepModel" class="ticker-model">{{ currentStepModel }}</span>
        <span v-if="totalTokens > 0" class="ticker-tokens">{{ totalTokens.toLocaleString() }} tokens</span>
      </div>
    </div>

    <!-- Pipeline Control Bar -->
    <div class="pipeline-control-bar" v-if="status !== 'completed' && !showGate">
      <div v-if="pipelineMessage" class="pipeline-indicator">
        <span class="pipeline-dot"></span>
        {{ pipelineMessage }}
      </div>
      <div class="pipeline-buttons">
        <button v-if="pipelinePaused && !showGate" class="ctl-btn advance" @click="advancePipeline" title="Approve and advance">Advance</button>
        <button v-if="pipelinePaused && !showGate" class="ctl-btn resume" @click="resumePipelineAction" title="Resume auto-advance">Resume Auto</button>
        <button v-if="!pipelinePaused && status === 'running'" class="ctl-btn pause" @click="pausePipelineAction" title="Pause at next step">Pause</button>
        <button v-if="status === 'running'" class="ctl-btn cancel" @click="cancelPipelineAction" title="Cancel entire review">Cancel</button>
        <button v-if="status === 'failed' || status === 'stopped'" class="ctl-btn retry" @click="retryPipeline" title="Retry from last checkpoint">Retry</button>
      </div>
    </div>

    <!-- Agent Cards -->
    <div v-if="agents.length > 0" class="agent-grid">
      <AgentCard
        v-for="agent in agents"
        :key="agent.id"
        :agent="agent"
        :streaming-text="displayStreams[agent.id] || ''"
      />
    </div>

    <!-- Deliberation Chat Transcript -->
    <div v-if="deliberationChat.length > 0" class="delib-chat-section">
      <h3 class="section-header">Deliberation Transcript</h3>
      <div class="delib-chat-feed">
        <div
          v-for="(msg, i) in deliberationChat"
          :key="i"
          class="chat-message"
        >
          <div class="chat-meta">
            <span class="chat-role">{{ msg.role }}</span>
            <span v-if="msg.round" class="chat-round">R{{ msg.round }}</span>
            <span class="chat-time">{{ msg.time }}</span>
          </div>
          <div class="chat-content">{{ msg.content.slice(0, 1000) }}{{ msg.content.length > 1000 ? '...' : '' }}</div>
        </div>
      </div>
    </div>

    <!-- PC Chair Review -->
    <div v-if="pcChairReview" class="card-chair-section">
      <h3 class="section-header">PC Chair Review</h3>
      <div class="card-chair-content" v-html="md(pcChairReview)"></div>
    </div>

    <!-- Events Log (collapsible) -->
    <div v-if="events.length > 0" class="events-section">
      <h3 @click="showEvents = !showEvents" class="collapsible">
        Activity Log ({{ events.length }})
        <span class="toggle">{{ showEvents ? '−' : '+' }}</span>
      </h3>
      <div v-if="showEvents" class="events-log">
        <div v-for="evt in events" :key="evt._id" class="event-entry">
          <span class="event-time">{{ evt.time }}</span>
          <span class="event-text">{{ evt.text }}</span>
        </div>
      </div>
    </div>

    <!-- Final Review Form (when complete) -->
    <FinalReview
      v-if="status === 'completed' && finalReview && Object.keys(finalReview).length > 0"
      ref="finalReviewRef"
      :session-id="sessionId"
      :initial-review="finalReview"
      :chair-model="chairModel"
    />

    <!-- Review Packet (when complete) -->
    <ReviewPacket v-if="packet" :packet="packet" />

    <!-- Error -->
    <div v-if="error" class="error-box">
      <strong>Error:</strong> {{ error }}
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { getSession, getReviewPacket, connectStream, pipelineAdvance, pipelinePause, pipelineResume, pipelineCancel, exportGraph } from '../api/kernel.js'
import { renderMarkdown } from '../utils/markdown.js'
import AgentCard from './AgentCard.vue'
import ReviewPacket from './ReviewPacket.vue'
import FinalReview from './ResultEditor.vue'

const md = renderMarkdown

const props = defineProps({
  sessionId: { type: String, required: true },
  conference: { type: String, default: 'hpdc26' },
})

const emit = defineEmits(['back', 'graph-update', 'graph-step-view', 'request-graph-focus', 'stage-changed'])

const stages = [
  {
    key: 'pre_review', label: 'Pre-Review',
    steps: [
      { key: 'parse', label: 'Parse' },
      { key: 'metadata', label: 'NLP Pre-pass' },
      { key: 'ontology', label: 'Ontology' },
      { key: 'extract', label: 'Extract' },
      { key: 'coref', label: 'Co-reference' },
      { key: 'verify', label: 'Verify' },
      { key: 'summarize', label: 'Summarize' },
    ],
  },
  {
    key: 'review', label: 'Review',
    steps: [
      { key: 'independent_reviews', label: 'Reviews' },
      { key: 'deliberation', label: 'Deliberation' },
      { key: 'meta_review', label: 'Meta-Review' },
      { key: 'pc_chair', label: 'PC Chair' },
    ],
  },
  {
    key: 'post_review', label: 'Post-Review',
    steps: [],
  },
]

const status = ref('running')
const currentStage = ref('')
const currentStep = ref('')
const agents = ref([])
const events = ref([])
const showEvents = ref(true)
const packet = ref(null)
const error = ref('')
const _rawStreams = {}
const displayStreams = reactive({})
let _streamDirty = false
function _flushStreams() {
  _streamDirty = false
  for (const k in _rawStreams) {
    if (displayStreams[k] !== _rawStreams[k]) {
      displayStreams[k] = _rawStreams[k]
    }
  }
}
const pipelineMessage = ref('')
const pipelinePaused = ref(false)
const showGate = ref(false)
const gateOntology = ref(null)
const gateStats = ref({ nodes: 0, edges: 0 })
const gateGraphStats = ref(null)
const gateReviewerSummary = ref('')
const showSummaryPreview = ref(false)
const pipelineSteps = reactive({})
const stepMeta = reactive({})
const expandedStep = ref('')
const deliberationChat = ref([])
const deliberationRound = ref({ current: 0, total: 0 })
const pcChairReview = ref('')
const finalReview = ref(null)
const finalReviewRef = ref(null)
const chairModel = ref('')
const startTime = Date.now()

const stepDescriptions = {
  parse: 'Extracting PDF structure',
  metadata: 'Analyzing sections, citations, equations',
  ontology: 'Generating review-specific entity types',
  extract: 'Building knowledge graph from paper',
  coref: 'Resolving abbreviations and aliases',
  verify: 'Auditing graph for grounding issues',
  summarize: 'Generating reviewer-facing summary',
  independent_reviews: 'Reviewers reading paper independently',
  deliberation: 'Reviewers debating assessments',
  meta_review: 'Synthesizing committee consensus',
  pc_chair: 'Writing author-facing review letter',
}

function shortModel(m) {
  if (!m) return ''
  const parts = m.split('/')
  return parts[parts.length - 1].slice(0, 24)
}
const elapsed = ref(0)
let ws = null
let pollTimer = null
let elapsedTimer = null

const statusColor = computed(() => {
  if (status.value === 'completed') return 'green'
  if (status.value === 'failed') return 'red'
  return 'yellow'
})

const statusText = computed(() => {
  if (status.value === 'completed') return 'Review complete'
  if (status.value === 'failed') return 'Review failed'
  if (showGate.value) return 'Waiting: inspect graph and proceed'
  if (status.value === 'running') {
    const stage = stages.find(s => s.key === currentStage.value)
    const step = stage?.steps.find(s => s.key === currentStep.value)
    if (stage && step) return `${stage.label}: ${step.label}`
    if (stage) return stage.label
    return 'Review in progress...'
  }
  return status.value
})

const totalTokens = computed(() => {
  return agents.value.reduce((sum, a) => sum + (a.tokens || 0), 0)
})

const currentStepModel = computed(() => {
  const step = pipelineSteps[currentStep.value]
  return step?.model ? shortModel(step.model) : ''
})

const currentStepDesc = computed(() => {
  return stepDescriptions[currentStep.value] || ''
})

const duration = computed(() => {
  const s = Math.floor(elapsed.value / 1000)
  const m = Math.floor(s / 60)
  const sec = s % 60
  return m > 0 ? `${m}m ${sec}s` : `${sec}s`
})

function stageClass(key) {
  const order = stages.map(s => s.key)
  const curIdx = order.indexOf(currentStage.value)
  const thisIdx = order.indexOf(key)
  if (status.value === 'completed') return 'done'
  if (thisIdx < curIdx) return 'done'
  if (thisIdx === curIdx) return 'active'
  return 'pending'
}

function stepClass(key) {
  if (!currentStage.value) return 'pending'
  const stage = stages.find(s => s.key === currentStage.value)
  if (!stage) return 'pending'
  const stepOrder = stage.steps.map(s => s.key)
  const curIdx = stepOrder.indexOf(currentStep.value)
  const thisIdx = stepOrder.indexOf(key)
  if (thisIdx < curIdx) return 'step-done'
  if (thisIdx === curIdx) return 'step-active'
  return 'step-pending'
}

let _evtId = 0
function addEvent(text) {
  const now = new Date()
  const time = now.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
  events.value.push({ _id: ++_evtId, time, text })
  if (events.value.length > 200) {
    events.value = events.value.slice(-200)
  }
}

function updateAgent(id, role, agentStatus, extra = {}) {
  const existing = agents.value.find(a => a.id === id)
  if (existing) {
    existing.status = agentStatus
    Object.assign(existing, extra)
  } else {
    agents.value.push({ id, role, status: agentStatus, model: extra.model || '', ...extra })
  }
}

async function doExportGraph() {
  try {
    const res = await exportGraph(props.sessionId)
    const url = URL.createObjectURL(res.data)
    const a = document.createElement('a')
    a.href = url
    a.download = `graph-${props.sessionId.slice(0, 8)}.json`
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    error.value = 'Failed to export graph: ' + (e.message || 'unknown')
  }
}

async function advancePipeline() {
  try {
    await pipelineAdvance(props.sessionId)
    showGate.value = false
    gateOntology.value = null
    pipelinePaused.value = false
    addEvent('Proceeding to review stage')
  } catch (e) {
    error.value = 'Failed to advance pipeline: ' + (e.message || 'unknown')
  }
}

async function pausePipelineAction() {
  try {
    await pipelinePause(props.sessionId)
    pipelinePaused.value = true
    addEvent('Pipeline paused by PC chair')
  } catch (e) {
    error.value = 'Failed to pause: ' + (e.message || 'unknown')
  }
}

async function resumePipelineAction() {
  try {
    await pipelineResume(props.sessionId)
    pipelinePaused.value = false
    addEvent('Pipeline resumed in auto mode')
  } catch (e) {
    error.value = 'Failed to resume: ' + (e.message || 'unknown')
  }
}

async function cancelPipelineAction() {
  try {
    await pipelineCancel(props.sessionId)
    status.value = 'failed'
    pipelinePaused.value = false
    showGate.value = false
    addEvent('Review cancelled by PC chair')
  } catch (e) {
    error.value = 'Failed to cancel: ' + (e.message || 'unknown')
  }
}

async function retryPipeline() {
  try {
    const { retrySession } = await import('../api/kernel.js')
    await retrySession(props.sessionId)
    status.value = 'running'
    error.value = ''
    addEvent('Retrying pipeline from last checkpoint...')
    // Reconnect the WebSocket stream
    const ws = connectStream(props.sessionId)
    ws.onmessage = (msg) => {
      try {
        const evt = JSON.parse(msg.data)
        handleStreamEvent(evt)
      } catch {}
    }
  } catch (e) {
    error.value = 'Retry failed: ' + (e.response?.data?.detail || e.message || 'unknown')
  }
}

function handleStreamEvent(evt) {
  // ── Stage/step transitions ──────────────────────────
  if (evt.type === 'stage_started') {
    currentStage.value = evt.stage
    currentStep.value = evt.step || ''
    pipelineMessage.value = evt.message || ''
    addEvent(evt.message || `Stage: ${evt.stage}`)
    emit('stage-changed', { stage: evt.stage, step: evt.step || '' })
  } else if (evt.type === 'step_started') {
    currentStage.value = evt.stage || currentStage.value
    currentStep.value = evt.step
    pipelineMessage.value = evt.message || ''
    pipelineSteps[evt.step] = {
      status: 'running',
      model: evt.model || '',
      startedAt: Date.now(),
      nodesAdded: 0,
      edgesAdded: 0,
    }
    addEvent(evt.message || `Step: ${evt.step}`)
    emit('stage-changed', { stage: currentStage.value, step: evt.step })
  } else if (evt.type === 'stage_complete') {
    if (evt.stage === 'pre_review') {
      showGate.value = true
      pipelineMessage.value = ''
      // Fetch graph stats and reviewer summary for the gate
      fetchGateData()
    }
    addEvent(`Stage complete: ${evt.stage}`)
  }

  // ── Legacy pipeline_phase (backward compat) ─────────
  else if (evt.type === 'pipeline_phase') {
    pipelineMessage.value = evt.message || ''
    // Map old phase names to new stage/step
    const phaseMap = {
      paper_processing: ['pre_review', 'metadata'],
      ontology: ['pre_review', 'ontology'],
      graph_building: ['pre_review', 'extract'],
      independent_review: ['review', 'independent_reviews'],
      deliberation: ['review', 'deliberation'],
      meta_review: ['review', 'meta_review'],
      pc_chair_review: ['review', 'pc_chair'],
    }
    const mapped = phaseMap[evt.phase]
    if (mapped) {
      currentStage.value = mapped[0]
      currentStep.value = mapped[1]
    }
    addEvent(evt.message || `Phase: ${evt.phase}`)
  }

  // ── Ontology ────────────────────────────────────────
  else if (evt.type === 'ontology_ready') {
    gateOntology.value = evt
    if (evt.paused) {
      pipelinePaused.value = true
    }
    pipelineMessage.value = ''
    addEvent(`Ontology ready: ${evt.paper_domain}, ${evt.entity_types?.length || 0} entity types`)
  }

  // ── Graph events ────────────────────────────────────
  else if (evt.type === 'graph_progress') {
    pipelineMessage.value = evt.message || ''
    if (evt.phase !== 'complete') addEvent(evt.message || 'Graph progress')
  } else if (evt.type === 'graph_updated') {
    emit('graph-update', { nodes: evt.nodes, edges: evt.edges })
    gateStats.value = { nodes: evt.node_count || 0, edges: evt.edge_count || 0 }
    if (evt.chunk && evt.total_chunks) {
      pipelineMessage.value = `Building graph: ${evt.node_count} nodes, ${evt.edge_count} edges (chunk ${evt.chunk}/${evt.total_chunks})`
    }
  } else if (evt.type === 'graph_complete') {
    pipelineMessage.value = ''
    gateStats.value = { nodes: evt.node_count || 0, edges: evt.edge_count || 0 }
    addEvent(`Knowledge graph: ${evt.node_count} nodes, ${evt.edge_count} edges`)
  } else if (evt.type === 'coref_complete') {
    gateStats.value = { nodes: evt.node_count || 0, edges: evt.edge_count || 0 }
    stepMeta.coref = { merged: evt.merged || 0, aliases: evt.aliases_created || 0 }
    addEvent(`Co-reference: ${evt.merged} merged, ${evt.aliases_created} aliases`)
  } else if (evt.type === 'verify_complete') {
    gateStats.value = { nodes: evt.node_count || 0, edges: evt.edge_count || 0 }
    stepMeta.verify = {
      grounding: evt.grounding_issues || 0,
      added: evt.missing_concepts_added || 0,
      connections: evt.missing_connections || 0,
    }
    addEvent(`Verification: ${evt.grounding_issues} grounding, ${evt.missing_concepts_added} added, ${evt.missing_connections || 0} connections`)
  } else if (evt.type === 'structural_graph') {
    emit('graph-update', { nodes: evt.nodes, edges: evt.edges })
    gateStats.value = { nodes: evt.node_count || 0, edges: evt.edge_count || 0 }
    addEvent(`Structural graph: ${evt.node_count} nodes`)
  } else if (evt.type === 'step_complete' || evt.type === 'step_state') {
    if (evt.step && pipelineSteps[evt.step]) {
      pipelineSteps[evt.step].status = evt.status || 'complete'
      pipelineSteps[evt.step].model = evt.model || pipelineSteps[evt.step].model
      pipelineSteps[evt.step].duration = evt.duration || 0
      pipelineSteps[evt.step].nodesAdded = evt.nodes_added || 0
      pipelineSteps[evt.step].edgesAdded = evt.edges_added || 0
    }
  }

  // ── Agent events ────────────────────────────────────
  else if (evt.type === 'agent_start') {
    updateAgent(evt.agent_id, evt.role || evt.agent_id, 'running', { model: evt.model })
    addEvent(`${evt.role || evt.agent_id} started`)
  } else if (evt.type === 'agent_done') {
    updateAgent(evt.agent_id, evt.role || evt.agent_id, 'done', {
      model: evt.model,
      duration: evt.duration_seconds,
      tokens: evt.tokens,
      completionTokens: evt.completion_tokens,
    })
    delete _rawStreams[evt.agent_id]
    delete displayStreams[evt.agent_id]
    const dur = evt.duration_seconds ? ` (${evt.duration_seconds}s)` : ''
    const tok = evt.tokens ? ` [${evt.tokens} tokens]` : ''
    addEvent(`${evt.role || evt.agent_id} finished${dur}${tok}`)
  } else if (evt.type === 'token') {
    const aid = evt.agent_id
    if (!_rawStreams[aid]) _rawStreams[aid] = ''
    _rawStreams[aid] += evt.chunk
    if (!_streamDirty) {
      _streamDirty = true
      requestAnimationFrame(_flushStreams)
    }
    return
  } else if (evt.type === 'agent_error') {
    updateAgent(evt.agent_id, evt.role || evt.agent_id, 'error')
    addEvent(`${evt.role || evt.agent_id} failed: ${evt.error || 'unknown'}`)
  }

  // ── Deliberation ────────────────────────────────────
  else if (evt.type === 'deliberation_turn') {
    const aid = evt.agent_id
    const content = evt.content || _rawStreams[aid] || ''
    if (content) {
      deliberationChat.value.push({
        role: evt.role || aid,
        content,
        round: evt.round || 0,
        time: new Date().toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }),
      })
    }
  } else if (evt.type === 'round_start') {
    deliberationRound.value.current = evt.round
    addEvent(`Deliberation round ${evt.round}${deliberationRound.value.total ? ' of ' + deliberationRound.value.total : ''}`)
  } else if (evt.type === 'consensus_detected' || evt.type === 'contested_detected') {
    deliberationRound.value.total = evt.effective_rounds || 0
  }

  // ── PC Chair ────────────────────────────────────────
  else if (evt.type === 'pc_chair_review_done') {
    // evt.review is now a structured dict (or a string for old sessions)
    if (typeof evt.review === 'object' && evt.review !== null) {
      finalReview.value = evt.review
      pcChairReview.value = evt.review.comments_for_authors || ''
    } else {
      pcChairReview.value = evt.review || ''
    }
    if (evt.model) chairModel.value = evt.model
    addEvent(`PC Chair review complete (${evt.duration_seconds || 0}s)`)
  }

  // ── Field Refinement Streaming ─────────────────────
  else if (evt.type === 'refine_token') {
    if (finalReviewRef.value) finalReviewRef.value.handleRefineToken(evt.field, evt.chunk)
  } else if (evt.type === 'refine_done') {
    if (finalReviewRef.value) finalReviewRef.value.handleRefineDone(evt.field, evt.content)
  } else if (evt.type === 'refine_error') {
    if (finalReviewRef.value) finalReviewRef.value.handleRefineError(evt.field, evt.detail)
  }

  // ── Pipeline control ───────────────────────────────
  else if (evt.type === 'pipeline_paused') {
    pipelinePaused.value = true
    addEvent(evt.message || 'Pipeline paused')
  } else if (evt.type === 'pipeline_resumed') {
    pipelinePaused.value = false
    addEvent(evt.message || 'Pipeline resumed')
  } else if (evt.type === 'pipeline_advanced') {
    pipelinePaused.value = false
    showGate.value = false
    gateOntology.value = null
    addEvent(evt.message || 'Pipeline advanced')
  } else if (evt.type === 'pipeline_cancelled') {
    status.value = 'failed'
    pipelinePaused.value = false
    showGate.value = false
    pipelineMessage.value = ''
    addEvent(evt.message || 'Review cancelled')
  } else if (evt.type === 'ontology_edited') {
    addEvent(evt.message || 'Ontology edited')
  }

  // ── Terminal ────────────────────────────────────────
  else if (evt.type === 'completed') {
    status.value = 'completed'
    currentStage.value = 'post_review'
    addEvent('Review session completed')
    fetchPacket()
  } else if (evt.type === 'error') {
    status.value = 'failed'
    error.value = evt.detail || 'Unknown error'
    addEvent(`Error: ${evt.detail}`)
  }

  // ── Engine phase_start (maps to step transitions) ──
  else if (evt.type === 'phase_start') {
    const stepMap = { deliberation: 'deliberation', meta_review: 'meta_review' }
    if (stepMap[evt.phase]) {
      currentStep.value = stepMap[evt.phase]
    }
  }
}

async function fetchPacket() {
  try {
    const res = await getReviewPacket(props.sessionId)
    packet.value = res.data
    // Populate final review from packet data on reconnect
    if (res.data.final_review && Object.keys(res.data.final_review).length > 0 && !finalReview.value) {
      finalReview.value = res.data.final_review
    }
    if (res.data.pc_chair_review && !pcChairReview.value) {
      pcChairReview.value = res.data.pc_chair_review
    }
  } catch (e) {
    console.error('Failed to fetch review packet:', e)
  }
}

async function fetchGateData() {
  try {
    const { getGraphSummary } = await import('../api/kernel.js')
    const res = await getGraphSummary(props.sessionId)
    gateGraphStats.value = res.data.stats
    gateReviewerSummary.value = res.data.summary
  } catch (e) {
    console.warn('Failed to fetch gate data:', e)
  }
}

const allStepCards = computed(() => {
  const preReviewSteps = [
    { key: 'parse', label: 'Parse' },
    { key: 'nlp_prepass', label: 'NLP Pre-pass' },
    { key: 'ontology', label: 'Ontology' },
    { key: 'extract', label: 'Extract' },
    { key: 'coref', label: 'Co-reference' },
    { key: 'verify', label: 'Verify' },
    { key: 'summarize', label: 'Summarize' },
  ]
  // Show cards for steps that have state (either from their own key or mapped key)
  return preReviewSteps.filter(s => {
    // metadata step reports as nlp_prepass in the backend
    if (s.key === 'nlp_prepass') return pipelineSteps.nlp_prepass || pipelineSteps.metadata
    return pipelineSteps[s.key]
  })
})

function resolveStep(key) {
  return pipelineSteps[key] || (key === 'nlp_prepass' ? pipelineSteps.metadata : null)
}

function stepCardClass(key) {
  const step = resolveStep(key)
  if (!step) return 'step-pending'
  if (step.status === 'running') return 'step-running'
  if (step.status === 'complete') return 'step-complete'
  if (step.status === 'failed') return 'step-failed'
  return 'step-pending'
}

function stepIconClass(key) {
  const step = resolveStep(key)
  if (!step) return ''
  return step.status || ''
}

function stepIcon(key) {
  const step = resolveStep(key)
  if (!step || step.status === 'pending') return '\u25CB'
  if (step.status === 'running') return '\u25CE'
  if (step.status === 'complete') return '\u25CF'
  if (step.status === 'failed') return '\u2715'
  return '\u25CB'
}

function formatStepDuration(key) {
  const step = resolveStep(key)
  if (!step?.duration) {
    if (step?.startedAt && step?.status === 'running') {
      const s = Math.floor((Date.now() - step.startedAt) / 1000)
      return `${s}s`
    }
    return ''
  }
  return `${Math.round(step.duration)}s`
}

function isStaleStep(key) {
  const step = pipelineSteps[key]
  return step?.status === 'pending' && step?.startedAt
}

async function rerunStep(stepName) {
  try {
    const { pipelineStepRun } = await import('../api/kernel.js')
    const res = await pipelineStepRun(props.sessionId, stepName)
    const data = res.data
    addEvent(`Re-running step: ${stepName}`)
    for (const s of (data.stale_steps || [])) {
      if (pipelineSteps[s]) {
        pipelineSteps[s].status = 'pending'
      }
    }
  } catch (e) {
    error.value = `Failed to re-run ${stepName}: ${e.message}`
  }
}

async function pollSession() {
  try {
    const res = await getSession(props.sessionId)
    const s = res.data
    if (s.status === 'completed' && status.value !== 'completed') {
      status.value = 'completed'
      currentStage.value = 'post_review'
      addEvent('Review session completed')
      fetchPacket()
      clearInterval(pollTimer)
    } else if (s.status === 'failed' && status.value !== 'failed') {
      status.value = 'failed'
      error.value = s.error || 'Unknown error'
      clearInterval(pollTimer)
    }
  } catch (e) {
    // polling failure is non-fatal
  }
}

onMounted(() => {
  elapsedTimer = setInterval(() => {
    if (status.value === 'running') {
      elapsed.value = Date.now() - startTime
    }
  }, 1000)

  try {
    ws = connectStream(props.sessionId)
    ws.onopen = () => {
      addEvent('Connected to review session')
    }
    ws.onmessage = (e) => {
      try {
        handleStreamEvent(JSON.parse(e.data))
      } catch (err) {
        console.error('WS parse error:', err)
      }
    }
    ws.onerror = () => {
      addEvent('WebSocket unavailable, polling instead')
      startPolling()
    }
    ws.onclose = () => {
      if (status.value === 'running') {
        startPolling()
      }
    }
  } catch (e) {
    startPolling()
  }
})

function startPolling() {
  if (!pollTimer) {
    pollTimer = setInterval(pollSession, 3000)
  }
}

onUnmounted(() => {
  if (ws) ws.close()
  if (pollTimer) clearInterval(pollTimer)
  if (elapsedTimer) clearInterval(elapsedTimer)
})
</script>

<style scoped>
.session-panel {
  margin-top: 8px;
  padding: 16px 20px;
  height: 100%;
  overflow-y: auto;
}

.back-btn {
  background: none;
  border: 1px solid #ddd;
  padding: 6px 14px;
  border-radius: 4px;
  font-size: 13px;
  cursor: pointer;
  margin-bottom: 24px;
  color: #666;
}

.back-btn:hover {
  border-color: #000;
  color: #000;
}

/* Stage bar */
.stage-bar {
  display: flex;
  gap: 8px;
  margin-bottom: 24px;
}

.stage-block {
  flex: 1;
  padding: 14px 16px;
  border: 1px solid #e0e0e0;
  border-radius: 5px;
  transition: all 0.3s;
}

.stage-header {
  display: flex;
  align-items: center;
  gap: 10px;
}

.stage-block.active {
  border-color: #000;
  background: #000;
  color: #fff;
}

.stage-block.done {
  border-color: #4a4;
  background: #f0f8f0;
  color: #2a2;
}

.stage-block.pending {
  color: #bbb;
}

.stage-num {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  border: 2px solid currentColor;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 700;
  flex-shrink: 0;
}

.stage-label {
  font-size: 13px;
  font-weight: 600;
}

/* Sub-steps inside active stage */
.sub-steps {
  display: flex;
  gap: 6px;
  margin-top: 10px;
  flex-wrap: wrap;
}

.sub-step {
  font-size: 10px;
  padding: 2px 8px;
  border-radius: 10px;
  font-weight: 600;
}

.sub-step.step-active {
  background: rgba(255,255,255,0.3);
  color: #fff;
}

.sub-step.step-done {
  background: rgba(255,255,255,0.15);
  color: rgba(255,255,255,0.7);
  text-decoration: line-through;
}

.sub-step.step-pending {
  color: rgba(255,255,255,0.35);
}

/* Status line */
.status-line {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 20px;
  font-size: 14px;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.status-dot.yellow {
  background: #e8a500;
  animation: pulse 1.5s infinite;
}

.status-dot.green { background: #4a4; }
.status-dot.red { background: #c44; }

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

.status-text { font-weight: 500; }

.duration {
  margin-left: auto;
  font-family: 'JetBrains Mono', monospace;
  font-size: 13px;
  color: #666;
}

/* Pre-Review Gate */
.pre-review-gate {
  border: 2px solid #e8a500;
  border-radius: 6px;
  padding: 20px;
  margin-bottom: 20px;
  background: #fffdf5;
}

.gate-header {
  font-size: 15px;
  font-weight: 700;
  margin-bottom: 8px;
}

.gate-summary {
  font-size: 13px;
  color: #444;
  line-height: 1.5;
  margin-bottom: 10px;
}

.gate-contribs {
  margin-bottom: 12px;
}

.contrib-label {
  font-size: 12px;
  font-weight: 600;
  color: #555;
  margin-bottom: 4px;
}

.gate-contribs ul {
  margin: 0;
  padding-left: 18px;
  font-size: 12px;
  color: #444;
}

.gate-contribs li { margin-bottom: 2px; }

.gate-actions {
  display: flex;
  gap: 10px;
  margin-top: 14px;
}

.gate-btn {
  padding: 8px 20px;
  font-size: 13px;
  font-weight: 600;
  border-radius: 4px;
  cursor: pointer;
  border: 2px solid;
}

.gate-btn.proceed {
  background: #000;
  color: #fff;
  border-color: #000;
}

.gate-btn.proceed:hover {
  background: #222;
}

.gate-btn.export-graph {
  border-color: #999;
  color: #555;
}

.gate-btn.export-graph:hover {
  border-color: #000;
  color: #000;
}

/* Ontology types (shared with gate) */
.ontology-types {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-bottom: 14px;
}

.type-column {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.type-col-header {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #888;
  margin-bottom: 4px;
}

.type-chip {
  font-size: 11px;
  padding: 4px 8px;
  border-radius: 3px;
  border: 1px solid #e0e0e0;
  background: #fff;
}

.type-chip strong {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
}

.type-desc {
  display: block;
  color: #888;
  font-size: 10px;
  margin-top: 1px;
}

/* Agent grid */
.agent-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 12px;
  margin-bottom: 24px;
}

/* Pipeline control bar */
.pipeline-control-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 14px;
  background: #f8f8f8;
  border: 1px solid #eaeaea;
  border-radius: 4px;
  margin-bottom: 16px;
}

.pipeline-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #555;
  flex: 1;
}

.pipeline-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #e8a500;
  animation: pulse 1.5s infinite;
  flex-shrink: 0;
}

.pipeline-buttons {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}

.ctl-btn {
  padding: 5px 12px;
  font-size: 12px;
  font-weight: 600;
  border-radius: 4px;
  cursor: pointer;
  border: 1px solid;
  transition: all 0.15s;
}

.ctl-btn.pause {
  background: #fff;
  color: #666;
  border-color: #ddd;
}

.ctl-btn.pause:hover {
  border-color: #e8a500;
  color: #a07000;
}

.ctl-btn.advance {
  background: #000;
  color: #fff;
  border-color: #000;
}

.ctl-btn.advance:hover {
  background: #222;
}

.ctl-btn.resume {
  background: #fff;
  color: #333;
  border-color: #ccc;
}

.ctl-btn.resume:hover {
  border-color: #000;
  color: #000;
}

.ctl-btn.cancel {
  background: #fff;
  color: #900;
  border-color: #ddd;
}

.ctl-btn.cancel:hover {
  border-color: #900;
  background: #fff5f5;
}

.ctl-btn.retry {
  background: #fff;
  color: #2563eb;
  border-color: #ddd;
}

.ctl-btn.retry:hover {
  border-color: #2563eb;
  background: #eff6ff;
}

/* Deliberation chat */
.delib-chat-section {
  margin-bottom: 24px;
  border: 1px solid #e8e8e8;
  border-radius: 5px;
  padding: 16px;
}

.section-header {
  font-size: 13px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #666;
  margin-bottom: 12px;
}

.delib-chat-feed {
  max-height: 400px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.chat-message {
  padding: 10px 12px;
  border-radius: 6px;
  border: 1px solid #f0f0f0;
  background: #fafafa;
}

.chat-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.chat-role {
  font-weight: 700;
  font-size: 12px;
  color: #333;
}

.chat-round {
  font-size: 10px;
  font-weight: 600;
  padding: 1px 5px;
  border-radius: 2px;
  background: #e8f0fe;
  color: #1a5ccc;
}

.chat-time {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  color: #bbb;
  margin-left: auto;
}

.chat-content {
  font-size: 13px;
  line-height: 1.5;
  color: #444;
  white-space: pre-wrap;
}

/* PC Chair review */
.card-chair-section {
  margin-bottom: 24px;
  border: 2px solid #000;
  border-radius: 6px;
  padding: 20px;
  background: #fafafa;
}

.card-chair-content {
  font-size: 14px;
  line-height: 1.7;
  color: #333;
}
.card-chair-content h2, .card-chair-content h3, .card-chair-content h4 {
  font-size: 14px; font-weight: 600; margin: 16px 0 8px; color: #222;
}
.card-chair-content strong { font-weight: 600; }
.card-chair-content code {
  font-family: 'JetBrains Mono', monospace; font-size: 12px;
  background: #f4f4f4; padding: 1px 4px; border-radius: 2px;
}
.card-chair-content li { margin-bottom: 4px; }
.card-chair-content p { margin: 0 0 8px; }
.card-chair-content .md-table { border-collapse: collapse; font-size: 12px; margin: 8px 0; width: 100%; }
.card-chair-content .md-table td { border: 1px solid #ddd; padding: 4px 8px; }
.card-chair-content .md-table tr:first-child td { font-weight: 600; background: #f8f8f8; }

/* Events */
.events-section {
  margin-bottom: 24px;
  border: 1px solid #e8e8e8;
  border-radius: 5px;
  padding: 16px;
}

.events-section h3 {
  font-size: 13px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #666;
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.events-log {
  margin-top: 12px;
  max-height: 200px;
  overflow-y: auto;
}

.event-entry {
  display: flex;
  gap: 12px;
  padding: 4px 0;
  font-size: 12px;
  border-bottom: 1px solid #f4f4f4;
}

.event-time {
  font-family: 'JetBrains Mono', monospace;
  color: #999;
  flex-shrink: 0;
}

.event-text { color: #444; }

.toggle {
  font-size: 16px;
  font-weight: 400;
}

/* Error */
.error-box {
  background: #fff5f5;
  border: 1px solid #fcc;
  border-radius: 5px;
  padding: 16px;
  font-size: 13px;
  color: #900;
}

/* Step cards */
.step-cards {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 16px;
}

.step-card {
  border: 1px solid #e8e8e8;
  border-radius: 4px;
  padding: 8px 12px;
  cursor: pointer;
  transition: all 0.15s;
}

.step-card:hover { border-color: #ccc; }
.step-card.step-running { border-color: #e8a500; background: #fffdf5; }
.step-card.step-complete { border-color: #4a4; background: #f8fcf8; }
.step-card.step-failed { border-color: #c44; background: #fff8f8; }

.step-card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}

.step-icon { font-size: 10px; flex-shrink: 0; }
.step-icon.running { color: #e8a500; }
.step-icon.complete { color: #4a4; }
.step-icon.failed { color: #c44; }

.step-card-label { font-weight: 600; color: #333; }

.step-model {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  color: #888;
  margin-left: auto;
}

.step-duration {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  color: #666;
}

.stale-badge {
  font-size: 9px;
  font-weight: 600;
  padding: 1px 5px;
  border-radius: 2px;
  background: #fff3cd;
  color: #856404;
}

.step-expand {
  font-size: 14px;
  color: #ccc;
  margin-left: 4px;
}

.step-card-details {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid #f0f0f0;
  font-size: 11px;
  color: #666;
}

.step-detail-row { margin-bottom: 2px; }

.step-card-actions {
  margin-top: 6px;
  display: flex;
  gap: 6px;
}

.step-action-btn {
  font-size: 11px;
  padding: 3px 10px;
  border: 1px solid #ddd;
  border-radius: 3px;
  background: #fff;
  color: #333;
  cursor: pointer;
}

.step-action-btn:hover { border-color: #000; color: #000; }

/* Gate stats grid */
.gate-stats-grid {
  display: flex;
  gap: 20px;
  margin: 12px 0;
  padding: 10px 0;
  border-top: 1px solid #eee;
  border-bottom: 1px solid #eee;
}
.gate-stat { display: flex; flex-direction: column; align-items: center; }
.gate-stat-val {
  font-size: 20px; font-weight: 700; color: #111;
  font-family: 'JetBrains Mono', monospace;
}
.gate-stat-lbl { font-size: 10px; color: #888; text-transform: uppercase; letter-spacing: 0.5px; }

/* Reviewer summary preview */
.gate-summary-preview {
  margin: 12px 0;
  border: 1px solid #e8e8e8;
  border-radius: 4px;
  overflow: hidden;
}
.summary-preview-header {
  padding: 8px 12px;
  font-size: 12px;
  font-weight: 600;
  color: #555;
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  background: #f8f8f8;
}
.summary-preview-header:hover { background: #f0f0f0; }
.summary-preview-body {
  padding: 12px;
  font-size: 11px;
  line-height: 1.5;
  color: #333;
  white-space: pre-wrap;
  font-family: 'JetBrains Mono', monospace;
  max-height: 400px;
  overflow-y: auto;
  margin: 0;
  background: #fff;
}

/* View Graph gate button */
.gate-btn.view-graph {
  background: #fff;
  color: #333;
  border-color: #ccc;
}
/* Sub-step model/duration tags */
.sub-step-model {
  font-family: 'JetBrains Mono', monospace;
  font-size: 8px;
  opacity: 0.6;
  margin-left: 2px;
}
.sub-step-dur {
  font-family: 'JetBrains Mono', monospace;
  font-size: 8px;
  opacity: 0.5;
}

.sub-step-round {
  font-family: 'JetBrains Mono', monospace;
  font-size: 8px;
  color: #e8a500;
  font-weight: 600;
}

.gate-btn.view-graph:hover { border-color: #000; color: #000; }

/* Activity Ticker */
.activity-ticker {
  padding: 10px 14px;
  background: #000;
  color: #fff;
  border-radius: 4px;
  margin-bottom: 16px;
}

.ticker-latest {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 500;
}

.ticker-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #e8a500;
  animation: pulse 1.5s infinite;
  flex-shrink: 0;
}

.ticker-context {
  display: flex;
  gap: 12px;
  margin-top: 6px;
  font-size: 11px;
  color: rgba(255,255,255,0.5);
  font-family: 'JetBrains Mono', monospace;
}

.ticker-desc { color: rgba(255,255,255,0.6); }
.ticker-model { color: rgba(255,255,255,0.4); }
.ticker-tokens { color: rgba(255,255,255,0.35); }

</style>
