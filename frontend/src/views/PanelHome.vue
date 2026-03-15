<template>
  <div class="panel-home">
    <header class="panel-header">
      <div class="header-left">
        <h1 class="logo">ProtoNeo</h1>
        <span class="product-tag">PC Panel</span>
      </div>
      <div class="header-right">
        <p class="tagline">Conference-calibrated pre-submission review</p>
        <router-link to="/settings" class="settings-link">Settings</router-link>
      </div>
    </header>

    <!-- Quality nudge with link to Settings -->
    <div v-if="!hasActiveModels" class="quality-nudge warn">
      <strong>No AI models configured.</strong>
      <router-link to="/settings">Open ProtoNeo Settings</router-link> to connect providers and select models before starting a review.
    </div>
    <div v-else class="quality-nudge">
      <strong>Review quality scales with compute.</strong>
      {{ activeModelCount }} model(s) active across {{ activeProviderCount }} provider(s).
      More providers, better models, and additional deliberation rounds produce stronger reviews.
      <router-link to="/settings">Manage providers</router-link>
    </div>

    <!-- Setup Phase -->
    <section class="setup-section">
      <div class="setup-grid">
        <!-- Venue Selection -->
        <div class="setup-card">
          <h2>1. Select Venue</h2>
          <div class="venue-selector">
            <div
              v-for="conf in conferences"
              :key="conf.slug"
              :class="['venue-option', { selected: selectedConference === conf.slug }]"
              @click="selectConference(conf.slug)"
            >
              <div class="venue-top">
                <div class="venue-name">{{ conf.short_name || conf.slug }}</div>
                <div class="venue-format-tag">{{ conf.format_style || 'ACM' }}</div>
              </div>
              <div class="venue-detail">{{ conf.location }}</div>
              <div class="venue-stats">
                <span>{{ conf.max_pages }}pp</span>
                <span>{{ conf.dual_anonymous ? 'Dual-anon' : 'Single-blind' }}</span>
                <span>{{ conf.agent_count }} agents</span>
                <span v-if="conf.optional_agent_count">+{{ conf.optional_agent_count }} optional</span>
              </div>
              <div v-if="conf.scope_summary" class="venue-scope">{{ conf.scope_summary }}</div>
            </div>
            <div v-if="conferences.length === 0" class="venue-option placeholder">
              Loading conferences...
            </div>
          </div>
        </div>

        <!-- Upload -->
        <div class="setup-card">
          <h2>2. Upload Manuscript</h2>
          <div class="mode-toggle">
            <button
              :class="['mode-btn', { active: uploadMode === 'single' }]"
              @click="uploadMode = 'single'"
            >Single Review</button>
            <button
              :class="['mode-btn', { active: uploadMode === 'batch' }]"
              @click="uploadMode = 'batch'"
            >Batch Graphs</button>
            <button
              :class="['mode-btn', { active: uploadMode === 'import' }]"
              @click="uploadMode = 'import'"
            >Import Graph</button>
          </div>
          <div
            v-if="uploadMode === 'single'"
            :class="['drop-zone', { active: dragOver, hasFile: !!selectedFile }]"
            @dragover.prevent="dragOver = true"
            @dragleave="dragOver = false"
            @drop.prevent="onDrop"
            @click="$refs.fileInput.click()"
          >
            <input
              ref="fileInput"
              type="file"
              accept=".pdf"
              style="display: none"
              @change="onFileSelect"
            />
            <template v-if="selectedFile">
              <div class="file-icon">PDF</div>
              <div class="file-name">{{ selectedFile.name }}</div>
              <div class="file-size">{{ formatSize(selectedFile.size) }}</div>
            </template>
            <template v-else>
              <div class="drop-prompt">Drop PDF here or click to browse</div>
            </template>
          </div>
          <div
            v-if="uploadMode === 'batch'"
            :class="['drop-zone batch-zone', { active: dragOver, hasFile: batchFiles.length > 0 }]"
            @dragover.prevent="dragOver = true"
            @dragleave="dragOver = false"
            @drop.prevent="onBatchDrop"
            @click="$refs.batchInput.click()"
          >
            <input
              ref="batchInput"
              type="file"
              accept=".pdf"
              multiple
              style="display: none"
              @change="onBatchSelect"
            />
            <template v-if="batchFiles.length > 0">
              <div class="batch-file-list">
                <div v-for="(f, i) in batchFiles" :key="i" class="batch-file-item">
                  <span class="file-icon small">PDF</span>
                  <span class="file-name">{{ f.name }}</span>
                  <span class="file-size">{{ formatSize(f.size) }}</span>
                  <button class="remove-btn" @click.stop="batchFiles.splice(i, 1)">&times;</button>
                </div>
              </div>
              <div class="batch-count">{{ batchFiles.length }} file(s) selected</div>
            </template>
            <template v-else>
              <div class="drop-prompt">Drop multiple PDFs or click to browse</div>
              <div class="drop-hint">Build knowledge graphs overnight, review next morning</div>
            </template>
          </div>
          <div
            v-if="uploadMode === 'import'"
            :class="['drop-zone', { active: dragOver, hasFile: !!importFile }]"
            @dragover.prevent="dragOver = true"
            @dragleave="dragOver = false"
            @drop.prevent="onImportDrop"
            @click="$refs.importInput.click()"
          >
            <input
              ref="importInput"
              type="file"
              accept=".json"
              style="display: none"
              @change="onImportSelect"
            />
            <template v-if="importFile">
              <div class="file-icon">JSON</div>
              <div class="file-name">{{ importFile.name }}</div>
              <div class="file-size">{{ formatSize(importFile.size) }}</div>
              <div v-if="importGraphStats" class="import-stats">
                {{ importGraphStats.nodes }} nodes, {{ importGraphStats.edges }} edges
              </div>
            </template>
            <template v-else>
              <div class="drop-prompt">Drop exported graph JSON here</div>
              <div class="drop-hint">Skip graph building, go straight to review</div>
            </template>
          </div>
        </div>
      </div>

      <!-- Review Panel: Agent Cards -->
      <div v-if="panelAgents.length > 0" class="panel-section">
        <h2 class="section-heading">
          3. Review Panel
          <span class="agent-count">{{ enabledAgentCount }} agents across {{ uniqueModelCount }} models</span>
        </h2>
        <div class="agent-assignment-grid">
          <div
            v-for="pa in panelAgents"
            :key="pa.id"
            :class="['agent-assign-card', { disabled: !pa.enabled }]"
          >
            <div class="aac-header">
              <div class="aac-role">
                {{ pa.role }}
                <span v-if="pa.isMeta" class="meta-badge">synthesizer</span>
                <span v-if="pa.optional" class="optional-badge">optional</span>
              </div>
              <button
                class="aac-toggle"
                :class="{ on: pa.enabled }"
                @click="pa.enabled = !pa.enabled"
              >{{ pa.enabled ? 'On' : 'Off' }}</button>
            </div>
            <div class="aac-focus">
              <span v-for="f in pa.focus" :key="f" class="focus-chip">{{ f }}</span>
            </div>
            <div class="aac-model">
              <select
                v-model="modelMap[pa.id]"
                :disabled="!pa.enabled"
                class="model-select"
              >
                <option
                  v-for="m in availableModels"
                  :key="m.model_id"
                  :value="m.model_id"
                >{{ modelLabel(m) }}</option>
              </select>
              <div class="model-info" v-if="modelMap[pa.id]">
                {{ modelDetail(modelMap[pa.id]) }}
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Graph Processing Models -->
      <div class="panel-section" v-if="availableModels.length > 0">
        <h2 class="section-heading">
          Graph Processing
          <span class="agent-count">Models for pre-review knowledge graph pipeline</span>
        </h2>
        <div class="graph-model-grid">
          <div v-for="step in graphSteps" :key="step.id" class="graph-model-card">
            <div class="gmc-header">
              <div class="gmc-role">{{ step.label }}</div>
              <div class="gmc-desc">{{ step.desc }}</div>
            </div>
            <select v-model="modelMap[step.id]" class="model-select">
              <option v-for="m in availableModels" :key="m.model_id" :value="m.model_id">{{ modelLabel(m) }}</option>
            </select>
          </div>
        </div>
      </div>

      <!-- User Instructions -->
      <div class="instructions-section">
        <h2 class="section-heading">4. Reviewer Instructions</h2>
        <textarea
          v-model="userInstructions"
          class="instructions-input"
          placeholder="Enter any specific instructions for the review panel. These will be injected as PC Chair directives into every reviewer's system prompt.

Examples:
• Focus particularly on scalability claims beyond 1024 nodes
• This is a resubmission; check if prior review concerns were addressed
• Pay attention to the comparison with [specific baseline]
• Evaluate whether the theoretical analysis in Section 3 is sound
• Be lenient on writing quality if technical contribution is strong"
          rows="5"
        ></textarea>
        <div class="instructions-hint">
          These instructions guide all agents throughout the pipeline, from ontology generation through meta-review.
        </div>
      </div>

      <!-- Paper Metadata Summary Card -->
      <div v-if="preflight?.metadata" class="metadata-card">
        <h3 class="metadata-header">Paper Summary</h3>
        <div class="metadata-title">{{ preflight.metadata.title || 'Untitled' }}</div>
        <div v-if="preflight.metadata.abstract" class="metadata-abstract">
          <span class="abstract-label">Abstract</span>
          {{ truncateAbstract(preflight.metadata.abstract) }}
          <button
            v-if="preflight.metadata.abstract.length > 300"
            class="abstract-toggle"
            @click="showFullAbstract = !showFullAbstract"
          >{{ showFullAbstract ? 'less' : 'more' }}</button>
        </div>
        <div class="metadata-stats">
          <div class="stat">
            <span class="stat-value">~{{ preflight.estimated_pages }}</span>
            <span class="stat-label">pages</span>
          </div>
          <div class="stat">
            <span class="stat-value">{{ preflight.metadata.estimated_word_count.toLocaleString() }}</span>
            <span class="stat-label">words</span>
          </div>
          <div class="stat">
            <span class="stat-value">{{ preflight.metadata.figure_count }}</span>
            <span class="stat-label">figures</span>
          </div>
          <div class="stat">
            <span class="stat-value">{{ preflight.metadata.table_count }}</span>
            <span class="stat-label">tables</span>
          </div>
          <div class="stat">
            <span class="stat-value">{{ preflight.metadata.reference_count }}</span>
            <span class="stat-label">references</span>
          </div>
        </div>
        <div v-if="preflight.metadata.sections.length" class="metadata-sections">
          <span class="sections-label">Sections:</span>
          <span v-for="(sec, si) in preflight.metadata.sections" :key="si" class="section-chip">{{ sec }}</span>
        </div>
      </div>

      <!-- Preflight Checks -->
      <div v-if="preflight" class="preflight-results">
        <h3 class="preflight-header">
          Preflight Check
          <span :class="['preflight-badge', preflight.block_count > 0 ? 'blocked' : preflight.warn_count > 0 ? 'warnings' : 'clear']">
            {{ preflight.block_count > 0 ? 'Blocked' : preflight.warn_count > 0 ? `${preflight.warn_count} Warning(s)` : 'Clear' }}
          </span>
        </h3>
        <div class="preflight-meta">
          ~{{ preflight.estimated_pages }} pages &middot; {{ Math.round(preflight.text_length / 1000) }}k chars
        </div>
        <div v-for="(check, ci) in preflight.checks" :key="ci" :class="['check-row', check.passed ? 'pass' : check.severity]">
          <span class="check-icon">{{ check.passed ? 'PASS' : check.severity === 'blocker' ? 'FAIL' : 'WARN' }}</span>
          <span class="check-name">{{ check.name }}</span>
          <span class="check-detail">{{ check.detail }}</span>
        </div>
      </div>

      <!-- Actions -->
      <div class="action-row">
        <template v-if="uploadMode === 'single'">
          <button
            v-if="!preflight"
            :class="['action-btn preflight-btn', { ready: canLaunch }]"
            :disabled="!canLaunch || preflighting"
            @click="runPreflightCheck"
          >
            {{ preflighting ? 'Checking...' : 'Run Preflight Check' }}
          </button>
          <button
            v-if="preflight"
            :class="['action-btn launch-btn', { ready: canLaunch && preflight.block_count === 0 }]"
            :disabled="!canLaunch || launching || preflight.block_count > 0"
            @click="doLaunchReview"
          >
            {{ launching ? 'Starting review...' : preflight.block_count > 0 ? 'Blocked by preflight' : 'Start Panel Review' }}
          </button>
        </template>
        <template v-if="uploadMode === 'batch'">
          <button
            :class="['action-btn launch-btn', { ready: batchFiles.length > 0 && selectedConference }]"
            :disabled="batchFiles.length === 0 || !selectedConference || launching"
            @click="launchBatch"
          >
            {{ launching ? 'Building graphs...' : `Build ${batchFiles.length} Graph${batchFiles.length !== 1 ? 's' : ''}` }}
          </button>
        </template>
        <template v-if="uploadMode === 'import'">
          <button
            :class="['action-btn launch-btn', { ready: !!importFile && selectedConference }]"
            :disabled="!importFile || !selectedConference || launching"
            @click="launchImport"
          >
            {{ launching ? 'Starting review...' : 'Review with Imported Graph' }}
          </button>
        </template>
      </div>
      <p v-if="launchError" class="error-msg">{{ launchError }}</p>
    </section>

    <!-- Recent Batches -->
    <section v-if="recentBatches.length > 0" class="history-section">
      <h2 class="history-header">Recent Batches</h2>
      <div class="session-list">
        <div
          v-for="b in recentBatches"
          :key="b.batch_id"
          class="session-row"
          @click="router.push({ name: 'BatchDashboard', params: { batchId: b.batch_id } })"
        >
          <span :class="['session-status-dot', b.status]"></span>
          <span class="session-id">{{ b.batch_id.slice(0, 8) }}</span>
          <span class="session-date">{{ b.session_count }} papers &middot; {{ formatDate(b.created_at) }}</span>
          <span :class="['session-badge', b.status]">{{ b.status }}</span>
        </div>
      </div>
    </section>

    <!-- Active Sessions -->
    <section v-if="activeSessions.length > 0" class="history-section active-section">
      <h2 class="history-header">Active Sessions</h2>
      <div class="session-list">
        <div
          v-for="sess in activeSessions"
          :key="sess.session_id"
          class="session-row active-row"
          @click="openSession(sess.session_id)"
        >
          <span class="session-status-dot running"></span>
          <span class="session-id">{{ sess.session_id.slice(0, 8) }}</span>
          <span class="session-date">{{ formatDate(sess.created_at) }}</span>
          <span class="session-badge running">running</span>
        </div>
      </div>
    </section>

    <!-- Recent Sessions -->
    <section v-if="completedSessions.length > 0" class="history-section">
      <h2 class="history-header">Recent Sessions</h2>
      <div class="session-list">
        <div
          v-for="sess in completedSessions"
          :key="sess.session_id"
          class="session-row"
          @click="openSession(sess.session_id)"
        >
          <span :class="['session-status-dot', sess.status]"></span>
          <span class="session-id">{{ sess.session_id.slice(0, 8) }}</span>
          <span class="session-date">{{ formatDate(sess.created_at) }}</span>
          <span :class="['session-badge', sess.status]">{{ sess.status }}</span>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { getConferences, getConference, getModels, startPanelReview, runPreflight, listSessions, getSettings, getActiveModelAssignments, startBatch, reviewWithGraph, listBatches } from '../api/kernel.js'

const router = useRouter()

const recentSessions = ref([])
const protoNeoSettings = ref({ active_models: {} })

const hasActiveModels = computed(() =>
  Object.values(protoNeoSettings.value.active_models || {}).some(v => v)
)
const activeModelCount = computed(() =>
  Object.values(protoNeoSettings.value.active_models || {}).filter(v => v).length
)
const activeProviderCount = computed(() =>
  new Set(
    Object.entries(protoNeoSettings.value.active_models || {})
      .filter(([, v]) => v)
      .map(([k]) => k)
  ).size
)

const conferences = ref([])
const availableModels = ref([])
const selectedConference = ref('hpdc26')
const selectedFile = ref(null)
const dragOver = ref(false)
const launching = ref(false)
const preflighting = ref(false)
const launchError = ref('')
const preflight = ref(null)
const showFullAbstract = ref(false)
const userInstructions = ref('')
const uploadMode = ref('single')  // 'single', 'batch', 'import'
const batchFiles = ref([])
const importFile = ref(null)
const importGraphStats = ref(null)
const recentBatches = ref([])

// Agent assignments built from conference profile
const panelAgents = ref([])
const modelMap = reactive({})

// Active model assignments from GET /api/settings/active-models.
// Format: {provider: {model_id, litellm_model, api_base, api_key_source}}
const activeAssignments = ref({})

// Build a list of provider/model_id strings from active assignments
const activeModelIds = computed(() => {
  const ids = []
  for (const [provider, info] of Object.entries(activeAssignments.value)) {
    if (info && info.model_id) {
      ids.push(`${provider}/${info.model_id}`)
    }
  }
  return ids
})

// Role-to-provider preference for smart defaults.
// Maps agent role IDs to preferred providers, tried in order.
const ROLE_PROVIDER_PREF = {
  technical: ['dynamo', 'zbook', 'mini'],
  skeptic: ['dynamo', 'openrouter', 'zbook'],
  novelty: ['mini', 'openrouter', 'dynamo'],
  clarity: ['mini', 'zbook', 'dynamo'],
  meta_reviewer: ['openrouter', 'zbook', 'dynamo'],
  meta: ['openrouter', 'zbook', 'dynamo'],
  artifact: ['mini', 'zbook', 'dynamo'],
}

const graphSteps = [
  { id: 'ontology', label: 'Ontology', desc: 'Generates paper-specific entity/edge types' },
  { id: 'extraction', label: 'Extraction', desc: 'Extracts entities and relationships per section' },
  { id: 'coref', label: 'Co-reference', desc: 'Resolves co-references and abbreviations' },
  { id: 'verification', label: 'Verification', desc: 'Grounding, completeness, consistency checks' },
]

function benchmarkTokensPerSecond(bench) {
  if (!bench) return null
  if (typeof bench.throughput === 'number') return bench.throughput
  return bench.throughput?.tokens_per_second || null
}

function normalizeModelId(modelId) {
  if (!modelId) return ''
  const models = availableModels.value || []
  if (!models.length) return modelId
  if (models.some(m => m.model_id === modelId)) return modelId

  const rawId = modelId.includes('/') ? modelId.split('/').slice(1).join('/') : modelId
  for (const [provider, info] of Object.entries(activeAssignments.value || {})) {
    if (info?.model_id !== rawId) continue
    const candidate = `${provider}/${rawId}`
    if (models.some(m => m.model_id === candidate)) return candidate
  }

  const suffixMatch = models.find(m => {
    const candidate = m.model_id || ''
    return candidate === rawId || candidate.endsWith(`/${rawId}`)
  })
  if (suffixMatch) return suffixMatch.model_id

  return models[0]?.model_id || modelId
}

function getModelDefault(roleId) {
  const assignments = activeAssignments.value
  const providers = Object.keys(assignments)

  if (providers.length > 0) {
    // Try preferred providers for this role
    const prefs = ROLE_PROVIDER_PREF[roleId] || []
    for (const prov of prefs) {
      const info = assignments[prov]
      if (info && info.model_id) {
        return normalizeModelId(`${prov}/${info.model_id}`)
      }
    }
    // Fall back to first available provider
    const first = providers[0]
    const info = assignments[first]
    if (info && info.model_id) {
      return normalizeModelId(`${first}/${info.model_id}`)
    }
  }

  // Last resort: first model from /api/models
  return normalizeModelId(availableModels.value[0]?.model_id || '')
}

const enabledAgentCount = computed(() => panelAgents.value.filter(a => a.enabled).length)
const uniqueModelCount = computed(() => {
  const enabled = panelAgents.value.filter(a => a.enabled)
  return new Set(enabled.map(a => modelMap[a.id])).size
})

const activeSessions = computed(() => recentSessions.value.filter(s => s.status === 'running' || s.status === 'created'))
const completedSessions = computed(() => recentSessions.value.filter(s => s.status !== 'running' && s.status !== 'created'))

const canLaunch = computed(() => {
  if (uploadMode.value === 'single') return selectedConference.value && selectedFile.value
  if (uploadMode.value === 'batch') return selectedConference.value && batchFiles.value.length > 0
  if (uploadMode.value === 'import') return selectedConference.value && importFile.value
  return false
})

watch([selectedFile, selectedConference], () => {
  preflight.value = null
})

watch([availableModels, activeAssignments], () => {
  for (const pa of panelAgents.value) {
    if (modelMap[pa.id]) {
      modelMap[pa.id] = normalizeModelId(modelMap[pa.id])
    }
  }
})

async function selectConference(slug) {
  selectedConference.value = slug
  try {
    const res = await getConference(slug)
    const profile = res.data
    buildPanelFromProfile(profile)
  } catch (e) {
    console.error('Failed to load conference profile:', e)
  }
}

function buildPanelFromProfile(profile) {
  const agents = []
  const pa = profile.panel_agents || {}
  for (const [id, def] of Object.entries(pa)) {
    agents.push({
      id,
      role: def.role || id,
      focus: def.focus || [],
      enabled: true,
      optional: false,
      isMeta: id === 'meta_reviewer' || id === 'meta',
    })
    modelMap[id] = normalizeModelId(getModelDefault(id))
  }
  const oa = profile.optional_agents || {}
  for (const [id, def] of Object.entries(oa)) {
    agents.push({
      id,
      role: def.role || id,
      focus: def.focus || [],
      enabled: false,
      optional: true,
      isMeta: false,
    })
    modelMap[id] = normalizeModelId(getModelDefault(id))
  }
  // Set graph processing model defaults (prefer fast local models)
  for (const step of graphSteps) {
    if (!modelMap[step.id]) {
      modelMap[step.id] = normalizeModelId(getModelDefault('technical'))
    }
  }
  panelAgents.value = agents
}

function modelLabel(m) {
  const mid = m.model_id || ''
  // Check if this model is an active assignment (mark it)
  const isActive = activeModelIds.value.includes(mid)
  const prefix = isActive ? '\u2713 ' : ''

  // Find benchmark data
  const benchmarks = protoNeoSettings.value.benchmark_results || []
  const bench = benchmarks.find(b => {
    // Match by full model_id or by provider+model_id combo
    const benchFull = b.provider ? `${b.provider}/${b.model_id}` : b.model_id
    return benchFull === mid || b.model_id === mid
  })
  if (bench) {
    const score = bench.total_score || ''
    const tps = benchmarkTokensPerSecond(bench)
    const speed = tps ? `${Math.round(tps)} t/s` : ''
    const parts = [mid]
    if (score) parts.push(`${score}/100`)
    if (speed) parts.push(speed)
    return prefix + parts.join(' \u00b7 ')
  }

  // Use display_name from /api/models if available
  if (m.display_name && m.display_name !== mid) {
    return prefix + `${mid} (${m.display_name})`
  }
  return prefix + mid
}

function modelDetail(modelId) {
  const resolvedModelId = normalizeModelId(modelId)
  if (!resolvedModelId) return ''
  // Extract provider from model_id
  const slashIdx = resolvedModelId.indexOf('/')
  const provider = slashIdx >= 0 ? resolvedModelId.slice(0, slashIdx) : ''

  // Check active assignments for routing info
  const assignment = activeAssignments.value[provider]
  if (assignment) {
    const parts = []
    parts.push(provider)
    if (assignment.api_key_source) parts.push(assignment.api_key_source)
    return parts.join(' \u00b7 ')
  }

  // Fall back to benchmark data
  const benchmarks = protoNeoSettings.value.benchmark_results || []
  const bench = benchmarks.find(b => {
    const benchFull = b.provider ? `${b.provider}/${b.model_id}` : b.model_id
    return benchFull === resolvedModelId || b.model_id === resolvedModelId
  })
  if (!bench) return provider || ''
  const parts = []
  if (bench.provider) parts.push(bench.provider)
  const tps = benchmarkTokensPerSecond(bench)
  if (tps) parts.push(`${Math.round(tps)} t/s`)
  if (bench.protoneo_class) parts.push(bench.protoneo_class)
  return parts.join(' \u00b7 ')
}

onMounted(async () => {
  try {
    const [confRes, modelRes, sessRes, settRes, batchRes] = await Promise.all([
      getConferences(),
      getModels(),
      listSessions(10),
      getSettings(),
      listBatches(5).catch(() => ({ data: { batches: [] } })),
    ])
    conferences.value = confRes.data.conferences
    availableModels.value = modelRes.data.models || []
    recentSessions.value = sessRes.data.sessions || []
    protoNeoSettings.value = settRes.data || { active_models: {} }
    recentBatches.value = batchRes.data.batches || []

    // Fetch active model assignments (provider routing with api_base)
    try {
      const amRes = await getActiveModelAssignments()
      activeAssignments.value = amRes.data || {}
    } catch (e) {
      console.warn('Active model assignments unavailable, using model list:', e.message)
    }

    // Load default conference profile for agent cards
    if (selectedConference.value) {
      selectConference(selectedConference.value)
    }
  } catch (e) {
    console.error('Failed to load config:', e)
  }
})

function openSession(sid) {
  router.push({ name: 'PanelReview', params: { sessionId: sid } })
}

function formatDate(isoStr) {
  if (!isoStr) return ''
  const d = new Date(isoStr)
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) +
    ' ' + d.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit' })
}

function onFileSelect(e) {
  const file = e.target.files[0]
  if (file && file.type === 'application/pdf') selectedFile.value = file
}

function onDrop(e) {
  dragOver.value = false
  const file = e.dataTransfer.files[0]
  if (file && file.type === 'application/pdf') selectedFile.value = file
}

function formatSize(bytes) {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / 1048576).toFixed(1) + ' MB'
}

function truncateAbstract(text) {
  if (!text) return ''
  if (showFullAbstract.value || text.length <= 300) return text
  return text.slice(0, 300).replace(/\s+\S*$/, '') + '...'
}

async function runPreflightCheck() {
  if (!canLaunch.value) return
  preflighting.value = true
  launchError.value = ''
  try {
    const res = await runPreflight(selectedFile.value, selectedConference.value)
    preflight.value = res.data
  } catch (e) {
    launchError.value = e.response?.data?.detail || e.message || 'Preflight check failed'
  } finally {
    preflighting.value = false
  }
}

async function doLaunchReview() {
  if (!canLaunch.value) return
  launching.value = true
  launchError.value = ''
  try {
    // Build model map from only enabled agents
    const enabledMap = {}
    for (const pa of panelAgents.value) {
      if (pa.enabled) enabledMap[pa.id] = normalizeModelId(modelMap[pa.id])
    }
    const res = await startPanelReview(
      selectedFile.value,
      selectedConference.value,
      enabledMap,
      2,
      userInstructions.value
    )
    const sid = res.data.session_id
    const query = { conference: selectedConference.value }
    if (preflight.value?.metadata) {
      query.metadata = encodeURIComponent(JSON.stringify(preflight.value.metadata))
    }
    router.push({ name: 'PanelReview', params: { sessionId: sid }, query })
  } catch (e) {
    launchError.value = e.response?.data?.detail || e.message || 'Failed to start review'
  } finally {
    launching.value = false
  }
}

function onBatchDrop(e) {
  dragOver.value = false
  const files = Array.from(e.dataTransfer.files).filter(f => f.type === 'application/pdf')
  batchFiles.value.push(...files)
}

function onBatchSelect(e) {
  const files = Array.from(e.target.files).filter(f => f.type === 'application/pdf')
  batchFiles.value.push(...files)
}

function onImportDrop(e) {
  dragOver.value = false
  const file = e.dataTransfer.files[0]
  if (file && file.name.endsWith('.json')) {
    importFile.value = file
    previewImportGraph(file)
  }
}

function onImportSelect(e) {
  const file = e.target.files[0]
  if (file) {
    importFile.value = file
    previewImportGraph(file)
  }
}

async function previewImportGraph(file) {
  try {
    const text = await file.text()
    const data = JSON.parse(text)
    const graph = data.graph || data
    const nodes = graph.nodes || []
    const edges = graph.edges || []
    importGraphStats.value = { nodes: nodes.length, edges: edges.length }
  } catch {
    importGraphStats.value = null
  }
}

async function launchBatch() {
  if (batchFiles.value.length === 0) return
  launching.value = true
  launchError.value = ''
  try {
    const enabledMap = {}
    for (const pa of panelAgents.value) {
      if (pa.enabled) enabledMap[pa.id] = normalizeModelId(modelMap[pa.id])
    }
    for (const step of graphSteps) {
      if (modelMap[step.id]) enabledMap[step.id] = normalizeModelId(modelMap[step.id])
    }
    const res = await startBatch(batchFiles.value, selectedConference.value, enabledMap)
    router.push({ name: 'BatchDashboard', params: { batchId: res.data.batch_id } })
  } catch (e) {
    launchError.value = e.response?.data?.detail || e.message || 'Failed to start batch'
  } finally {
    launching.value = false
  }
}

async function launchImport() {
  if (!importFile.value) return
  launching.value = true
  launchError.value = ''
  try {
    const enabledMap = {}
    for (const pa of panelAgents.value) {
      if (pa.enabled) enabledMap[pa.id] = normalizeModelId(modelMap[pa.id])
    }
    const res = await reviewWithGraph(
      importFile.value,
      selectedConference.value,
      enabledMap,
      2,
      userInstructions.value
    )
    router.push({ name: 'PanelReview', params: { sessionId: res.data.session_id } })
  } catch (e) {
    launchError.value = e.response?.data?.detail || e.message || 'Failed to start review'
  } finally {
    launching.value = false
  }
}
</script>

<style scoped>
.panel-home {
  max-width: 1100px;
  margin: 0 auto;
  padding: 40px 24px;
}

.panel-header {
  margin-bottom: 48px;
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
}

.header-left {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.header-right {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 4px;
}

.settings-link {
  font-size: 13px;
  color: #666;
  text-decoration: none;
}
.settings-link:hover {
  color: #000;
}

.quality-nudge {
  background: #f8f9fa;
  border: 1px solid #e8e8e8;
  border-radius: 6px;
  padding: 12px 18px;
  font-size: 13px;
  color: #555;
  margin-bottom: 28px;
  line-height: 1.5;
}
.quality-nudge.warn {
  background: #fffbf0;
  border-color: #f5e6c0;
  color: #8a6d00;
}
.quality-nudge a {
  color: #0066cc;
  text-decoration: none;
  font-weight: 600;
}
.quality-nudge a:hover {
  text-decoration: underline;
}

.logo {
  font-family: 'Space Grotesk', sans-serif;
  font-size: 28px;
  font-weight: 700;
  letter-spacing: -0.5px;
}

.product-tag {
  font-size: 13px;
  font-weight: 500;
  background: #000;
  color: #fff;
  padding: 2px 10px;
  border-radius: 3px;
  letter-spacing: 0.5px;
}

.tagline { font-size: 15px; color: #666; }

.setup-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
  margin-bottom: 28px;
}

.setup-card {
  border: 1px solid #e0e0e0;
  padding: 24px;
  border-radius: 6px;
}

.setup-card h2,
.section-heading {
  font-size: 14px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 16px;
  color: #333;
}

/* Venue selector */
.venue-selector { display: flex; flex-direction: column; gap: 8px; }

.venue-option {
  padding: 14px 16px;
  border: 2px solid #e8e8e8;
  border-radius: 5px;
  cursor: pointer;
  transition: border-color 0.15s;
}

.venue-option:hover { border-color: #999; }
.venue-option.selected { border-color: #000; background: #fafafa; }
.venue-option.placeholder { color: #999; border-style: dashed; cursor: default; }
.venue-top { display: flex; justify-content: space-between; align-items: center; }
.venue-name { font-weight: 600; font-size: 15px; }
.venue-format-tag {
  font-size: 10px; font-weight: 600; padding: 1px 6px; border-radius: 2px;
  background: #f0f0f0; color: #666; letter-spacing: 0.3px;
}
.venue-detail { font-size: 13px; color: #666; margin-top: 2px; }
.venue-stats {
  display: flex; gap: 8px; margin-top: 6px;
  font-size: 11px; color: #888;
}
.venue-stats span {
  padding: 1px 5px; background: #f8f8f8; border-radius: 2px;
}
.venue-scope {
  font-size: 11px; color: #999; margin-top: 6px; line-height: 1.4;
  overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
}

/* Drop zone */
.drop-zone {
  border: 2px dashed #ccc;
  border-radius: 6px;
  padding: 48px 24px;
  text-align: center;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
  min-height: 160px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}

.drop-zone:hover, .drop-zone.active { border-color: #000; background: #fafafa; }
.drop-zone.hasFile { border-style: solid; border-color: #000; }
.drop-prompt { color: #999; font-size: 14px; }

.file-icon {
  font-size: 12px; font-weight: 700; background: #000; color: #fff;
  padding: 4px 10px; border-radius: 3px; margin-bottom: 8px; letter-spacing: 1px;
}

.file-name { font-weight: 600; font-size: 14px; word-break: break-all; }
.file-size { font-size: 12px; color: #999; margin-top: 4px; }

/* ── Review Panel: Agent Assignment Cards ── */
.panel-section { margin-bottom: 28px; }

.section-heading {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.agent-count {
  font-size: 12px;
  font-weight: 400;
  color: #888;
  text-transform: none;
  letter-spacing: 0;
}

.agent-assignment-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 12px;
}

.agent-assign-card {
  border: 1px solid #e0e0e0;
  border-radius: 6px;
  padding: 14px 16px;
  transition: border-color 0.15s, opacity 0.15s;
}

.agent-assign-card.disabled {
  opacity: 0.45;
  border-style: dashed;
}

.aac-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.aac-role {
  font-size: 13px;
  font-weight: 700;
  color: #111;
}

.meta-badge, .optional-badge {
  font-size: 9px; font-weight: 600; padding: 1px 5px; border-radius: 2px;
  margin-left: 6px; vertical-align: middle; letter-spacing: 0.3px;
}
.meta-badge { background: #e8f0fe; color: #1a5ccc; }
.optional-badge { background: #f0f0f0; color: #888; }

.aac-toggle {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 10px;
  border-radius: 3px;
  cursor: pointer;
  border: 1px solid #ccc;
  background: #f5f5f5;
  color: #999;
  transition: all 0.15s;
}

.aac-toggle.on { border-color: #4a4; color: #4a4; background: #f0f8f0; }
.aac-toggle:hover { border-color: #000; color: #000; }

.aac-focus {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 10px;
}

.focus-chip {
  font-size: 10px;
  padding: 1px 6px;
  background: #f0f0f0;
  border-radius: 2px;
  color: #666;
}

.aac-model {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.model-select {
  width: 100%;
  padding: 6px 8px;
  border: 1px solid #ddd;
  border-radius: 4px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  background: #fff;
  color: #333;
}

.model-select:disabled {
  background: #f8f8f8;
  color: #bbb;
}

.model-info {
  font-size: 10px;
  color: #999;
  font-family: 'JetBrains Mono', monospace;
}

/* Paper metadata card */
.metadata-card {
  border: 1px solid #e0e0e0;
  border-radius: 6px;
  padding: 20px 24px;
  margin-bottom: 16px;
  background: #fafafa;
}

.metadata-header {
  font-size: 14px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.5px; margin-bottom: 12px; color: #333;
}

.metadata-title {
  font-size: 17px; font-weight: 700; line-height: 1.4;
  margin-bottom: 12px; color: #111;
}

.metadata-abstract { font-size: 13px; line-height: 1.6; color: #444; margin-bottom: 16px; }
.abstract-label { font-weight: 600; color: #333; margin-right: 6px; }
.abstract-toggle {
  background: none; border: none; color: #0066cc; cursor: pointer;
  font-size: 12px; padding: 0 2px; text-decoration: underline;
}

.metadata-stats {
  display: flex; gap: 24px; margin-bottom: 14px;
  padding: 10px 0; border-top: 1px solid #eaeaea; border-bottom: 1px solid #eaeaea;
}

.stat { display: flex; flex-direction: column; align-items: center; }

.stat-value {
  font-size: 18px; font-weight: 700; color: #111;
  font-family: 'JetBrains Mono', monospace;
}

.stat-label { font-size: 11px; color: #888; text-transform: uppercase; letter-spacing: 0.5px; }

.metadata-sections { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.sections-label { font-size: 12px; font-weight: 600; color: #666; margin-right: 4px; }

.section-chip {
  font-size: 11px; padding: 2px 8px; background: #fff; border: 1px solid #ddd;
  border-radius: 3px; color: #555; font-family: 'JetBrains Mono', monospace;
}

/* Preflight results */
.preflight-results {
  border: 1px solid #e0e0e0; border-radius: 6px;
  padding: 20px 24px; margin-bottom: 20px;
}

.preflight-header {
  font-size: 14px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.5px; display: flex; align-items: center;
  gap: 12px; margin-bottom: 8px;
}

.preflight-badge {
  font-size: 11px; font-weight: 700; padding: 2px 10px;
  border-radius: 3px; letter-spacing: 0.5px;
}

.preflight-badge.clear { background: #e8f5e9; color: #2a2; }
.preflight-badge.warnings { background: #fff8e1; color: #a07000; }
.preflight-badge.blocked { background: #ffebee; color: #900; }

.preflight-meta { font-size: 12px; color: #888; margin-bottom: 12px; }

.check-row {
  display: flex; align-items: flex-start; gap: 10px;
  padding: 6px 0; font-size: 13px; border-bottom: 1px solid #f4f4f4;
}

.check-row:last-child { border-bottom: none; }

.check-icon {
  font-size: 10px; font-weight: 700; padding: 1px 6px;
  border-radius: 3px; flex-shrink: 0; margin-top: 1px;
}

.check-row.pass .check-icon { background: #e8f5e9; color: #2a2; }
.check-row.warning .check-icon { background: #fff8e1; color: #a07000; }
.check-row.blocker .check-icon { background: #ffebee; color: #900; }
.check-row.info .check-icon { background: #f0f0f0; color: #666; }

.check-name { font-weight: 600; min-width: 100px; flex-shrink: 0; }
.check-detail { color: #555; }

/* Action row */
.action-row { display: flex; gap: 12px; }

.action-btn {
  flex: 1; padding: 16px; font-size: 15px; font-weight: 600;
  border: 2px solid #ccc; background: #fff; color: #999;
  border-radius: 6px; cursor: not-allowed; transition: all 0.15s; letter-spacing: 0.3px;
}

.action-btn.ready { cursor: pointer; }
.action-btn:disabled { opacity: 0.6; }
.preflight-btn.ready { border-color: #666; background: #fff; color: #333; }
.preflight-btn.ready:hover { border-color: #000; color: #000; }
.launch-btn.ready { border-color: #000; background: #000; color: #fff; }
.launch-btn.ready:hover { background: #222; }

/* User instructions */
.instructions-section { margin-bottom: 28px; }

.instructions-input {
  width: 100%;
  padding: 12px 14px;
  border: 1px solid #e0e0e0;
  border-radius: 6px;
  font-size: 13px;
  font-family: 'Space Grotesk', system-ui, sans-serif;
  line-height: 1.5;
  resize: vertical;
  color: #333;
  transition: border-color 0.15s;
  box-sizing: border-box;
}

.instructions-input:focus {
  outline: none;
  border-color: #000;
}

.instructions-input::placeholder { color: #bbb; font-size: 12px; }

.instructions-hint {
  font-size: 11px;
  color: #999;
  margin-top: 6px;
}

/* Active sessions */
.active-section { margin-top: 32px; padding-top: 24px; border-top: 1px solid #eaeaea; }
.active-row { border-color: #e8a500 !important; background: #fffdf5; }

.error-msg { color: #c00; font-size: 13px; margin-top: 12px; text-align: center; }

/* Session history */
.history-section { margin-top: 40px; padding-top: 32px; border-top: 1px solid #eaeaea; }

.history-header {
  font-size: 14px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.5px; color: #333; margin-bottom: 12px;
}

.session-list { display: flex; flex-direction: column; gap: 4px; }

.session-row {
  display: flex; align-items: center; gap: 12px; padding: 10px 14px;
  border: 1px solid #f0f0f0; border-radius: 4px; cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}

.session-row:hover { border-color: #ccc; background: #fafafa; }

.session-status-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.session-status-dot.completed { background: #4a4; }
.session-status-dot.running { background: #e8a500; }
.session-status-dot.failed { background: #c44; }
.session-status-dot.created { background: #ccc; }

.session-id {
  font-family: 'JetBrains Mono', monospace;
  font-size: 13px; font-weight: 600; color: #333;
}

.session-date { font-size: 12px; color: #888; flex: 1; }

.session-badge {
  font-size: 11px; font-weight: 600; padding: 2px 8px;
  border-radius: 3px; text-transform: uppercase; letter-spacing: 0.3px;
}

.session-badge.completed { background: #e8f5e9; color: #2a2; }
.session-badge.running { background: #fff8e1; color: #a07000; }
.session-badge.failed { background: #ffebee; color: #900; }
.session-badge.created { background: #f5f5f5; color: #666; }

.graph-model-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 10px;
}

.graph-model-card {
  border: 1px solid #e0e0e0;
  border-radius: 6px;
  padding: 12px 14px;
}

.gmc-header { margin-bottom: 8px; }
.gmc-role { font-size: 13px; font-weight: 700; color: #111; }
.gmc-desc { font-size: 11px; color: #888; margin-top: 2px; }

/* Mode toggle */
.mode-toggle {
  display: flex;
  gap: 4px;
  margin-bottom: 12px;
  background: #f5f5f5;
  border-radius: 4px;
  padding: 3px;
}

.mode-btn {
  flex: 1;
  font-size: 11px;
  font-weight: 600;
  padding: 5px 10px;
  border: none;
  border-radius: 3px;
  background: transparent;
  color: #888;
  cursor: pointer;
  transition: all 0.15s;
}

.mode-btn.active {
  background: #fff;
  color: #111;
  box-shadow: 0 1px 3px rgba(0,0,0,0.1);
}

.mode-btn:hover:not(.active) { color: #333; }

/* Batch file list */
.batch-zone { min-height: 120px; }

.batch-file-list {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 4px;
  text-align: left;
}

.batch-file-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 8px;
  background: #f8f8f8;
  border-radius: 3px;
  font-size: 12px;
}

.batch-file-item .file-name { flex: 1; }

.file-icon.small {
  font-size: 9px;
  padding: 2px 5px;
  margin-bottom: 0;
}

.remove-btn {
  background: none;
  border: none;
  color: #999;
  cursor: pointer;
  font-size: 16px;
  padding: 0 4px;
  line-height: 1;
}

.remove-btn:hover { color: #c00; }

.batch-count {
  font-size: 12px;
  color: #888;
  margin-top: 8px;
}

.drop-hint {
  font-size: 11px;
  color: #bbb;
  margin-top: 4px;
}

/* Import stats */
.import-stats {
  font-size: 12px;
  color: #4a4;
  margin-top: 4px;
  font-weight: 600;
}
</style>
