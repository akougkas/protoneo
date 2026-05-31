<template>
  <div class="panel-home pn-fade-in">
    <header class="panel-header">
      <div class="header-left">
        <div class="brand-block">
          <h1 class="logo">PROTONEO</h1>
          <span class="version-tag">v0.1.0</span>
        </div>
        <div class="brand-divider"></div>
        <span class="product-tag">{{ appDisplayName }}</span>
      </div>
      <div class="header-right">
        <router-link to="/settings" class="settings-link">
          <span class="settings-icon">&#x2699;</span> Settings
        </router-link>
      </div>
    </header>

    <div v-if="!hasActiveModels" class="sys-banner sys-banner--warn">
      <span class="sys-banner-indicator"></span>
      <div class="sys-banner-content">
        <strong>No AI models configured.</strong>
        <router-link to="/settings">Open Settings</router-link> to connect providers and select models.
      </div>
    </div>
    <div v-else class="sys-banner">
      <span class="sys-banner-indicator sys-banner-indicator--ok"></span>
      <div class="sys-banner-content">
        {{ activeModelCount }} model(s) active across {{ activeProviderCount }} provider(s).
        Quality scales with compute.
        <router-link to="/settings">Manage</router-link>
      </div>
    </div>

    <!-- Setup Phase -->
    <section class="setup-section">
      <div class="setup-grid">
        <!-- Venue Selection -->
        <div class="setup-card">
          <h2 class="setup-heading"><span class="pn-section-num">01</span> Select Venue</h2>
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
          <h2 class="setup-heading"><span class="pn-section-num">02</span> Upload Manuscript</h2>
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
              :class="['mode-btn', { active: uploadMode === 'batch-review' }]"
              @click="uploadMode = 'batch-review'"
            >Batch Review</button>
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
            v-if="uploadMode === 'batch-review'"
            :class="['drop-zone batch-zone', { active: dragOver, hasFile: batchReviewFiles.length > 0 }]"
            @dragover.prevent="dragOver = true"
            @dragleave="dragOver = false"
            @drop.prevent="onBatchReviewDrop"
            @click="$refs.batchReviewInput.click()"
          >
            <input
              ref="batchReviewInput"
              type="file"
              accept=".pdf"
              multiple
              style="display: none"
              @change="onBatchReviewSelect"
            />
            <template v-if="batchReviewFiles.length > 0">
              <div class="batch-file-list">
                <div v-for="(f, i) in batchReviewFiles" :key="i" class="batch-file-item">
                  <span class="file-icon small">PDF</span>
                  <span class="file-name">{{ f.name }}</span>
                  <span class="file-size">{{ formatSize(f.size) }}</span>
                  <button class="remove-btn" @click.stop="batchReviewFiles.splice(i, 1)">&times;</button>
                </div>
              </div>
              <div class="batch-count">{{ batchReviewFiles.length }} file(s) selected</div>
            </template>
            <template v-else>
              <div class="drop-prompt">Drop multiple PDFs or click to browse</div>
              <div class="drop-hint">Full review per paper, processed one at a time</div>
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
          <div v-if="uploadMode === 'import' && savedGraphSessions.length > 0" class="saved-graphs">
            <div class="saved-graphs-header">Saved Graphs</div>
            <div
              v-for="sess in savedGraphSessions"
              :key="sess.session_id"
              class="saved-graph-row"
            >
              <div class="saved-graph-main">
                <span class="saved-graph-title">{{ graphSessionTitle(sess) }}</span>
                <span class="saved-graph-meta">
                  {{ sess.knowledge_graph_stats.node_count }} nodes,
                  {{ sess.knowledge_graph_stats.edge_count }} edges
                </span>
              </div>
              <button
                class="saved-graph-btn"
                :disabled="!selectedConference || launching"
                @click.stop="launchSavedGraph(sess)"
              >
                Review
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- Parser Selection (advanced, optional) -->
      <div v-if="availableParsers.length > 0 && uploadMode === 'single'" class="panel-section parser-section">
        <details>
          <summary class="parser-toggle">Advanced: PDF Parser</summary>
          <div class="parser-options">
            <select v-model="selectedParser" class="parser-select">
              <option value="">Auto (highest priority)</option>
              <option v-for="p in availableParsers" :key="p.name" :value="p.name">{{ p.name }} (priority {{ p.priority }})</option>
            </select>
          </div>
        </details>
      </div>

      <!-- Preset Selector -->
      <div v-if="presets.length > 0 && panelAgents.length > 0" class="panel-section preset-section">
        <h2 class="section-heading">
          Model Preset
          <span class="agent-count">Pre-configured model assignments</span>
        </h2>
        <div class="preset-row">
          <select v-model="activePresetName" class="preset-select" @change="selectPreset(activePresetName)">
            <option value="">Custom</option>
            <option v-for="p in presets" :key="p.name" :value="p.name">{{ p.name }}</option>
          </select>
          <span class="preset-desc" v-if="activePresetName">
            {{ presets.find(p => p.name === activePresetName)?.description }}
          </span>
          <span class="preset-desc" v-else>Manual model selection below</span>
        </div>
      </div>

      <!-- Review Panel: Agent Cards -->
      <div v-if="panelAgents.length > 0" class="panel-section">
        <h2 class="section-heading">
          <span class="pn-section-num">03</span> Review Panel
          <span class="agent-count">{{ enabledAgentCount }} agents / {{ uniqueModelCount }} models</span>
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
                @change="syncRoleReasoning(pa.id)"
              >
                <option
                  v-for="m in routableModels"
                  :key="modelFullId(m)"
                  :value="modelFullId(m)"
                >{{ modelLabel(m) }}</option>
              </select>
              <div class="model-info" v-if="modelMap[pa.id]">
                {{ modelDetail(modelMap[pa.id]) }}
              </div>
              <select
                v-if="supportsReasoningEffort(modelMap[pa.id])"
                v-model="reasoningMap[pa.id]"
                class="model-select reasoning-select"
                :disabled="!pa.enabled"
              >
                <option value="">Provider default reasoning</option>
                <option v-for="level in reasoningOptions(modelMap[pa.id])" :key="level" :value="level">{{ level }}</option>
              </select>
            </div>
          </div>
        </div>
      </div>

      <!-- Graph Processing Models -->
      <div class="panel-section" v-if="routableModels.length > 0">
        <h2 class="section-heading">
          Graph Processing
          <span class="agent-count">Local providers are preferred; manual override is allowed</span>
        </h2>
        <div class="graph-model-grid">
          <div v-for="step in graphSteps" :key="step.id" class="graph-model-card">
            <div class="gmc-header">
              <div class="gmc-role">{{ step.label }}</div>
              <div class="gmc-desc">{{ step.desc }}</div>
            </div>
            <select v-model="modelMap[step.id]" class="model-select">
              <option v-for="m in graphModels" :key="modelFullId(m)" :value="modelFullId(m)">{{ modelLabel(m) }}</option>
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
          placeholder="Enter any specific instructions for the review panel. These will be injected as review-chair directives into every reviewer's system prompt.

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
            {{ launching ? 'Starting review...' : preflight.block_count > 0 ? 'Blocked by preflight' : 'Start Review' }}
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
        <template v-if="uploadMode === 'batch-review'">
          <button
            :class="['action-btn launch-btn', { ready: batchReviewFiles.length > 0 && selectedConference }]"
            :disabled="batchReviewFiles.length === 0 || !selectedConference || launching"
            @click="launchBatchReview"
          >
            {{ launching ? 'Starting reviews...' : `Review ${batchReviewFiles.length} Paper${batchReviewFiles.length !== 1 ? 's' : ''}` }}
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
          @click="router.push({ name: 'Batch', params: { batchId: b.batch_id } })"
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
import { ref, reactive, computed, inject, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { getConferences, getConference, getModels, startReview, runPreflight, listSessions, getSettings, getActiveModelAssignments, startBatch, startBatchReview, reviewWithGraph, listBatches, getPresets, activatePreset, getParsers, exportGraph } from '../api/kernel.js'

const router = useRouter()
const activeApp = inject('activeApp', ref(null))

const appDisplayName = computed(() => activeApp.value?.display_name || 'Paper Review')
const appDescription = computed(() => activeApp.value?.description || 'Conference-calibrated pre-submission review')

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
const selectedConference = ref('')
const selectedFile = ref(null)
const dragOver = ref(false)
const launching = ref(false)
const preflighting = ref(false)
const launchError = ref('')
const preflight = ref(null)
const showFullAbstract = ref(false)
const userInstructions = ref('')
const uploadMode = ref('single')  // 'single', 'batch', 'batch-review', 'import'
const batchFiles = ref([])
const batchReviewFiles = ref([])
const availableParsers = ref([])
const selectedParser = ref('')
const importFile = ref(null)
const importGraphStats = ref(null)
const recentBatches = ref([])

// Presets
const presets = ref([])
const activePresetName = ref('')

// Agent assignments built from conference profile
const panelAgents = ref([])
const modelMap = reactive({})
const reasoningMap = reactive({})

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
  technical: ['lan-dynamo', 'dynamo', 'zbook', 'lan-mini', 'mini'],
  skeptic: ['lan-mini', 'mini', 'lan-dynamo', 'dynamo', 'zbook'],
  novelty: ['lan-mini', 'mini', 'lan-dynamo', 'dynamo', 'zbook'],
  clarity: ['lan-dynamo', 'dynamo', 'zbook', 'lan-mini', 'mini'],
  meta_reviewer: ['lan-dynamo', 'dynamo', 'zbook', 'lan-mini', 'mini'],
  meta: ['lan-dynamo', 'dynamo', 'zbook', 'lan-mini', 'mini'],
  artifact: ['lan-mini', 'mini', 'zbook', 'lan-dynamo', 'dynamo'],
}

const graphSteps = [
  { id: 'ontology', label: 'Ontology', desc: 'Generates paper-specific entity/edge types' },
  { id: 'extraction', label: 'Extraction', desc: 'Extracts entities and relationships per section' },
  { id: 'coref', label: 'Co-reference', desc: 'Resolves co-references and abbreviations' },
  { id: 'verification', label: 'Verification', desc: 'Grounding, completeness, consistency checks' },
]

// Graph pipeline steps use local models only. Subscription tokens
// (OpenAI) are reserved for review roles.
const _SUBSCRIPTION_PROVIDERS = new Set(['openai'])  // anthropic removed
const routableModels = computed(() =>
  availableModels.value.filter(m => modelIsRoutable(m))
)
const localModels = computed(() =>
  routableModels.value.filter(m => !_SUBSCRIPTION_PROVIDERS.has(modelProviderId(m)))
)
const graphModels = computed(() => [
  ...localModels.value,
  ...routableModels.value.filter(m => _SUBSCRIPTION_PROVIDERS.has(modelProviderId(m))),
])

function modelProviderId(model) {
  return model.provider_id || model.provider || ''
}

function modelIsRoutable(model) {
  return model?.availability !== 'unsupported'
}

function modelFullId(model) {
  return model.provider_model_id || model.qualified_id || model.model_id || ''
}

function modelById(modelId) {
  const resolved = normalizeModelId(modelId)
  return availableModels.value.find(m => modelFullId(m) === resolved) || null
}

function supportsReasoningEffort(modelId) {
  return Boolean(modelById(modelId)?.supports_reasoning_effort)
}

function reasoningOptions(modelId) {
  return modelById(modelId)?.supported_reasoning_efforts || []
}

function providerFromModelId(modelId) {
  return modelId?.includes('/') ? modelId.split('/', 1)[0] : ''
}

function syncRoleReasoning(roleId) {
  const selected = normalizeModelId(modelMap[roleId])
  if (!supportsReasoningEffort(selected)) {
    delete reasoningMap[roleId]
    return
  }
  if (!reasoningMap[roleId]) {
    const provider = providerFromModelId(selected)
    reasoningMap[roleId] = activeAssignments.value?.[provider]?.reasoning_effort || ''
  }
}

function benchmarkTokensPerSecond(bench) {
  if (!bench) return null
  if (typeof bench.throughput === 'number') return bench.throughput
  return bench.throughput?.tokens_per_second || null
}

function normalizeModelId(modelId) {
  if (!modelId) return ''
  const models = routableModels.value || []
  if (!models.length) return modelId
  if (models.some(m => modelFullId(m) === modelId)) return modelId

  const rawId = modelId.includes('/') ? modelId.split('/').slice(1).join('/') : modelId
  for (const [provider, info] of Object.entries(activeAssignments.value || {})) {
    if (info?.model_id !== rawId) continue
    const candidate = `${provider}/${rawId}`
    if (models.some(m => modelFullId(m) === candidate)) return candidate
  }

  const suffixMatch = models.find(m => {
    const candidate = modelFullId(m) || ''
    return candidate === rawId || candidate.endsWith(`/${rawId}`)
  })
  if (suffixMatch) return modelFullId(suffixMatch)

  return modelFullId(models[0]) || modelId
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
  return normalizeModelId(modelFullId(routableModels.value[0]) || '')
}

function getGraphDefault() {
  const local = localModels.value[0]
  if (local) return normalizeModelId(modelFullId(local))
  return getModelDefault('technical')
}

const enabledAgentCount = computed(() => panelAgents.value.filter(a => a.enabled).length)
const uniqueModelCount = computed(() => {
  const enabled = panelAgents.value.filter(a => a.enabled)
  return new Set(enabled.map(a => modelMap[a.id])).size
})

const activeSessions = computed(() => recentSessions.value.filter(s => s.status === 'running' || s.status === 'created'))
const completedSessions = computed(() => recentSessions.value.filter(s => s.status !== 'running' && s.status !== 'created'))
const savedGraphSessions = computed(() =>
  recentSessions.value.filter(s =>
    s.status !== 'running'
    && s.status !== 'created'
    && s.knowledge_graph_stats
    && s.knowledge_graph_stats.node_count > 0
  )
)

const canLaunch = computed(() => {
  if (uploadMode.value === 'single') return selectedConference.value && selectedFile.value
  if (uploadMode.value === 'batch') return selectedConference.value && batchFiles.value.length > 0
  if (uploadMode.value === 'batch-review') return selectedConference.value && batchReviewFiles.value.length > 0
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
      syncRoleReasoning(pa.id)
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
    syncRoleReasoning(id)
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
    syncRoleReasoning(id)
  }
  // Set graph processing model defaults (prefer fast local models)
  for (const step of graphSteps) {
    if (!modelMap[step.id]) {
      modelMap[step.id] = normalizeModelId(getGraphDefault())
    }
  }
  panelAgents.value = agents
}

function applyPresetAssignments(assignments) {
  if (!assignments || typeof assignments !== 'object') return
  for (const [key, modelId] of Object.entries(assignments)) {
    const resolved = normalizeModelId(modelId)
    if (resolved) {
      modelMap[key] = resolved
      syncRoleReasoning(key)
    }
  }
}

async function selectPreset(name) {
  activePresetName.value = name
  const preset = presets.value.find(p => p.name === name)
  if (preset) {
    applyPresetAssignments(preset.assignments)
  }
  try {
    await activatePreset(name)
  } catch (e) {
    console.warn('Failed to persist preset selection:', e.message)
  }
}

function modelLabel(m) {
  const mid = modelFullId(m)
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
  const suffix = []
  if (m.context_length) suffix.push(`${Math.round(m.context_length / 1000)}K ctx`)
  if (m.availability === 'unverified') suffix.push('unverified')
  if (m.is_free) suffix.push('free')
  if (m.display_name && m.display_name !== mid) {
    return prefix + `${mid} (${[m.display_name, ...suffix].join(' \u00b7 ')})`
  }
  return prefix + (suffix.length ? `${mid} (${suffix.join(' \u00b7 ')})` : mid)
}

function modelDetail(modelId) {
  const resolvedModelId = normalizeModelId(modelId)
  if (!resolvedModelId) return ''
  // Extract provider from model_id
  const slashIdx = resolvedModelId.indexOf('/')
  const provider = slashIdx >= 0 ? resolvedModelId.slice(0, slashIdx) : ''
  const catalog = modelById(resolvedModelId)

  // Check active assignments for routing info
  const assignment = activeAssignments.value[provider]
  if (assignment) {
    const parts = []
    parts.push(provider)
    if (assignment.api_key_source) parts.push(assignment.api_key_source)
    if (catalog?.supports_reasoning_effort) parts.push('reasoning')
    return parts.join(' \u00b7 ')
  }

  if (catalog) {
    const parts = [provider || catalog.provider_id]
    if (catalog.context_length) parts.push(`${Math.round(catalog.context_length / 1000)}K ctx`)
    if (catalog.supports_reasoning_effort) parts.push('reasoning')
    if (catalog.is_free) parts.push('free')
    if (catalog.availability === 'unverified') parts.push('unverified catalog')
    return parts.filter(Boolean).join(' \u00b7 ')
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

function buildReviewModelMap() {
  const enabledMap = {}
  for (const pa of panelAgents.value) {
    if (!pa.enabled) continue
    const model = normalizeModelId(modelMap[pa.id])
    if (!model) continue
    const effort = reasoningMap[pa.id] || ''
    enabledMap[pa.id] = effort ? { model_id: model, reasoning_effort: effort } : model
  }
  return enabledMap
}

function addGraphModelMap(target) {
  for (const step of graphSteps) {
    if (modelMap[step.id]) target[step.id] = normalizeModelId(modelMap[step.id])
  }
  return target
}

function graphSessionTitle(sess) {
  return sess.config?.metadata?.paper_title
    || sess.config?.metadata?.filename
    || sess.session_id.slice(0, 8)
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

    // Load presets
    try {
      const presetRes = await getPresets()
      presets.value = presetRes.data.presets || []
      activePresetName.value = presetRes.data.active_preset || ''
    } catch (e) {
      console.warn('Presets unavailable:', e.message)
    }

    // Load available parsers
    try {
      const parserRes = await getParsers()
      const byExt = parserRes.data.parsers || {}
      const pdfParsers = byExt['.pdf'] || []
      availableParsers.value = pdfParsers
    } catch (e) {
      console.warn('Parsers unavailable:', e.message)
    }

    // Load default conference profile for agent cards
    if (selectedConference.value) {
      await selectConference(selectedConference.value)
    }

    // Auto-apply active preset after conference profile sets default modelMap
    if (activePresetName.value) {
      const preset = presets.value.find(p => p.name === activePresetName.value)
      if (preset) applyPresetAssignments(preset.assignments)
    }
  } catch (e) {
    console.error('Failed to load config:', e)
  }
})

function openSession(sid) {
  router.push({ name: 'Session', params: { sessionId: sid } })
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
    // Build model map from enabled agents + graph processing steps
    const enabledMap = addGraphModelMap(buildReviewModelMap())
    const res = await startReview(
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
    router.push({ name: 'Session', params: { sessionId: sid }, query })
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

function onBatchReviewDrop(e) {
  dragOver.value = false
  const files = Array.from(e.dataTransfer.files).filter(f => f.type === 'application/pdf')
  batchReviewFiles.value.push(...files)
}

function onBatchReviewSelect(e) {
  const files = Array.from(e.target.files).filter(f => f.type === 'application/pdf')
  batchReviewFiles.value.push(...files)
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
    const enabledMap = addGraphModelMap(buildReviewModelMap())
    const res = await startBatch(batchFiles.value, selectedConference.value, enabledMap)
    router.push({ name: 'Batch', params: { batchId: res.data.batch_id } })
  } catch (e) {
    launchError.value = e.response?.data?.detail || e.message || 'Failed to start batch'
  } finally {
    launching.value = false
  }
}

async function launchBatchReview() {
  if (batchReviewFiles.value.length === 0) return
  launching.value = true
  launchError.value = ''
  try {
    const enabledMap = addGraphModelMap(buildReviewModelMap())
    const res = await startBatchReview(
      batchReviewFiles.value,
      selectedConference.value,
      enabledMap,
      2,
      userInstructions.value
    )
    router.push({ name: 'Batch', params: { batchId: res.data.batch_id } })
  } catch (e) {
    launchError.value = e.response?.data?.detail || e.message || 'Failed to start batch review'
  } finally {
    launching.value = false
  }
}

async function launchImport() {
  if (!importFile.value) return
  launching.value = true
  launchError.value = ''
  try {
    const enabledMap = buildReviewModelMap()
    const res = await reviewWithGraph(
      importFile.value,
      selectedConference.value,
      enabledMap,
      2,
      userInstructions.value
    )
    router.push({ name: 'Session', params: { sessionId: res.data.session_id } })
  } catch (e) {
    launchError.value = e.response?.data?.detail || e.message || 'Failed to start review'
  } finally {
    launching.value = false
  }
}

async function launchSavedGraph(sess) {
  if (!selectedConference.value || !sess?.session_id) return
  launching.value = true
  launchError.value = ''
  try {
    const enabledMap = buildReviewModelMap()
    const graphRes = await exportGraph(sess.session_id)
    const graphFile = new File(
      [graphRes.data],
      `${sess.session_id}_graph.json`,
      { type: 'application/json' }
    )
    const res = await reviewWithGraph(
      graphFile,
      selectedConference.value,
      enabledMap,
      2,
      userInstructions.value
    )
    router.push({ name: 'Session', params: { sessionId: res.data.session_id } })
  } catch (e) {
    launchError.value = e.response?.data?.detail || e.message || 'Failed to launch saved graph review'
  } finally {
    launching.value = false
  }
}
</script>

<style scoped>
/* ═══════════════════════════════════════════════════════════
   HOME VIEW — Session launcher
   ═══════════════════════════════════════════════════════════ */

.panel-home {
  max-width: 1100px;
  margin: 0 auto;
  padding: var(--pn-space-7) var(--pn-space-5);
}

/* ── Header / Brand ── */
.panel-header {
  margin-bottom: var(--pn-space-7);
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-bottom: var(--pn-space-5);
  border-bottom: 1px solid var(--pn-border);
}

.header-left {
  display: flex;
  align-items: center;
  gap: var(--pn-space-4);
}

.brand-block {
  display: flex;
  align-items: baseline;
  gap: var(--pn-space-2);
}

.logo {
  font-family: var(--pn-mono);
  font-size: 20px;
  font-weight: 700;
  letter-spacing: 0.18em;
  color: var(--pn-text);
}

.version-tag {
  font-family: var(--pn-mono);
  font-size: 10px;
  font-weight: 400;
  color: var(--pn-text-ghost);
  letter-spacing: 0.02em;
}

.brand-divider {
  width: 1px;
  height: 18px;
  background: var(--pn-border-strong);
}

.product-tag {
  font-family: var(--pn-serif);
  font-size: 14px;
  font-weight: 500;
  font-style: italic;
  color: var(--pn-text-secondary);
  letter-spacing: 0;
}

.header-right {
  display: flex;
  align-items: center;
  gap: var(--pn-space-3);
}

.settings-link {
  font-size: 11px;
  color: var(--pn-text-muted);
  text-decoration: none;
  letter-spacing: 0.04em;
  display: flex;
  align-items: center;
  gap: var(--pn-space-1);
  padding: var(--pn-space-2) var(--pn-space-3);
  border: 1px solid var(--pn-border);
  transition: all var(--pn-duration) var(--pn-ease);
}
.settings-link:hover {
  border-color: var(--pn-text);
  color: var(--pn-text);
}
.settings-icon { font-size: 13px; }

/* ── System banner ── */
.sys-banner {
  display: flex;
  align-items: flex-start;
  gap: var(--pn-space-3);
  padding: var(--pn-space-3) var(--pn-space-4);
  margin-bottom: var(--pn-space-6);
  border: 1px solid var(--pn-border);
  font-size: 12px;
  color: var(--pn-text-secondary);
  line-height: 1.6;
}
.sys-banner--warn {
  border-color: var(--pn-warn);
  border-left-width: 3px;
}
.sys-banner-indicator {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--pn-warn);
  margin-top: 6px;
  flex-shrink: 0;
}
.sys-banner-indicator--ok {
  background: var(--pn-ok);
}
.sys-banner-content { flex: 1; }
.sys-banner a {
  color: var(--pn-accent);
  font-weight: 600;
}

/* ── Setup grid ── */
.setup-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--pn-space-5);
  margin-bottom: var(--pn-space-6);
}

.setup-card {
  border: 1px solid var(--pn-border);
  padding: var(--pn-space-5);
  background: var(--pn-surface);
}

.setup-heading,
.setup-card h2,
.section-heading {
  font-family: var(--pn-mono);
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: var(--pn-space-4);
  color: var(--pn-text-secondary);
  display: flex;
  align-items: baseline;
  gap: var(--pn-space-2);
}

/* ── Venue selector ── */
.venue-selector { display: flex; flex-direction: column; gap: var(--pn-space-2); }

.venue-option {
  padding: var(--pn-space-3) var(--pn-space-4);
  border: 1px solid var(--pn-border);
  cursor: pointer;
  transition: all var(--pn-duration) var(--pn-ease);
  position: relative;
}
.venue-option::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 0;
  background: var(--pn-accent);
  transition: width var(--pn-duration) var(--pn-ease);
}
.venue-option:hover { border-color: var(--pn-border-strong); }
.venue-option:hover::before { width: 3px; }
.venue-option.selected { border-color: var(--pn-text); }
.venue-option.selected::before { width: 3px; background: var(--pn-text); }
.venue-option.placeholder { color: var(--pn-text-muted); border-style: dashed; cursor: default; }

.venue-top { display: flex; justify-content: space-between; align-items: center; }
.venue-name { font-weight: 600; font-size: 13px; }
.venue-format-tag {
  font-family: var(--pn-mono);
  font-size: 9px; font-weight: 600; padding: 2px 6px;
  background: var(--pn-accent-dim); color: var(--pn-accent);
  letter-spacing: 0.06em; text-transform: uppercase;
}
.venue-detail { font-size: 12px; color: var(--pn-text-secondary); margin-top: 2px; }
.venue-stats {
  display: flex; gap: var(--pn-space-2); margin-top: var(--pn-space-2);
  font-size: 10px; color: var(--pn-text-muted);
}
.venue-stats span {
  padding: 1px 5px; border: 1px solid var(--pn-border);
}
.venue-scope {
  font-family: var(--pn-serif);
  font-size: 11px; color: var(--pn-text-muted); margin-top: var(--pn-space-2);
  line-height: 1.5; font-style: italic;
  overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
}

/* ── Drop zone ── */
.drop-zone {
  border: 1px dashed var(--pn-border-strong);
  padding: var(--pn-space-7) var(--pn-space-5);
  text-align: center;
  cursor: pointer;
  transition: all var(--pn-duration) var(--pn-ease);
  min-height: 160px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: var(--pn-bg);
}
.drop-zone:hover, .drop-zone.active {
  border-color: var(--pn-accent);
  background: var(--pn-accent-dim);
}
.drop-zone.hasFile { border-style: solid; border-color: var(--pn-text); background: var(--pn-surface); }
.drop-prompt { color: var(--pn-text-muted); font-size: 12px; }

.file-icon {
  font-size: 10px; font-weight: 700; background: var(--pn-text); color: var(--pn-bg);
  padding: 3px 8px; margin-bottom: var(--pn-space-2); letter-spacing: 0.1em;
}
.file-name { font-weight: 600; font-size: 13px; word-break: break-all; }
.file-size { font-size: 11px; color: var(--pn-text-muted); margin-top: 2px; }

/* ── Panel sections ── */
.panel-section { margin-bottom: var(--pn-space-6); }
.panel-section--muted { opacity: 0.5; }
.no-local-hint { font-size: 11px; color: var(--pn-text-muted); margin: var(--pn-space-2) 0 0; }

/* ── Parser section ── */
.parser-section { padding: 0; margin-bottom: var(--pn-space-4); }
.parser-toggle {
  cursor: pointer; font-size: 11px; color: var(--pn-text-muted);
  padding: var(--pn-space-2) 0; letter-spacing: 0.02em;
}
.parser-toggle:hover { color: var(--pn-text); }
.parser-options { padding: var(--pn-space-2) 0; }
.parser-select { width: 100%; max-width: 300px; }

/* ── Preset section ── */
.preset-section { margin-bottom: var(--pn-space-5); }
.preset-row { display: flex; align-items: center; gap: var(--pn-space-3); flex-wrap: wrap; }
.preset-select { min-width: 180px; }
.preset-desc { font-size: 11px; color: var(--pn-text-muted); }

.agent-count {
  font-size: 10px;
  font-weight: 400;
  color: var(--pn-text-muted);
  text-transform: none;
  letter-spacing: 0.02em;
  font-family: var(--pn-mono);
}

/* ── Agent assignment grid ── */
.agent-assignment-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
  gap: var(--pn-space-3);
}

.agent-assign-card {
  border: 1px solid var(--pn-border);
  padding: var(--pn-space-3) var(--pn-space-4);
  transition: all var(--pn-duration) var(--pn-ease);
  background: var(--pn-surface);
}
.agent-assign-card:hover { border-color: var(--pn-border-strong); }

.agent-assign-card.disabled {
  opacity: 0.35;
  border-style: dashed;
}

.aac-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--pn-space-2);
}

.aac-role {
  font-family: var(--pn-mono);
  font-size: 12px;
  font-weight: 700;
  color: var(--pn-text);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.meta-badge, .optional-badge {
  font-family: var(--pn-mono);
  font-size: 8px; font-weight: 600; padding: 1px 5px;
  margin-left: 6px; vertical-align: middle; letter-spacing: 0.06em;
  text-transform: uppercase;
}
.meta-badge { background: var(--pn-accent-dim); color: var(--pn-accent); }
.optional-badge { border: 1px solid var(--pn-border); color: var(--pn-text-muted); }

.aac-toggle {
  font-size: 10px;
  font-weight: 600;
  padding: 2px 8px;
  cursor: pointer;
  border: 1px solid var(--pn-border);
  background: transparent;
  color: var(--pn-text-muted);
  transition: all var(--pn-duration) var(--pn-ease);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.aac-toggle.on { border-color: var(--pn-ok); color: var(--pn-ok); background: var(--pn-ok-dim); }
.aac-toggle:hover { border-color: var(--pn-text); color: var(--pn-text); }

.aac-focus {
  display: flex;
  flex-wrap: wrap;
  gap: 3px;
  margin-bottom: var(--pn-space-2);
}

.focus-chip {
  font-size: 9px;
  padding: 1px 5px;
  border: 1px solid var(--pn-border);
  color: var(--pn-text-muted);
  letter-spacing: 0.02em;
}

.aac-model {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.model-select {
  width: 100%;
  font-size: 11px;
}

.reasoning-select {
  margin-top: 4px;
}

.model-select:disabled {
  background: var(--pn-bg);
  color: var(--pn-text-ghost);
}

.model-info {
  font-size: 9px;
  color: var(--pn-text-muted);
}

/* ── Paper metadata ── */
.metadata-card {
  border: 1px solid var(--pn-border);
  padding: var(--pn-space-5);
  margin-bottom: var(--pn-space-4);
  background: var(--pn-surface);
}

.metadata-header {
  font-family: var(--pn-mono);
  font-size: 10px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.08em; margin-bottom: var(--pn-space-3);
  color: var(--pn-text-muted);
}

.metadata-title {
  font-family: var(--pn-serif);
  font-size: 17px; font-weight: 600; line-height: 1.4;
  margin-bottom: var(--pn-space-3); color: var(--pn-text);
}

.metadata-abstract {
  font-family: var(--pn-serif);
  font-size: 13px; line-height: 1.7; color: var(--pn-text-secondary);
  margin-bottom: var(--pn-space-4);
}
.abstract-label { font-weight: 600; color: var(--pn-text); margin-right: 4px; }
.abstract-toggle {
  background: none; border: none; color: var(--pn-accent); cursor: pointer;
  font-size: 11px; padding: 0; font-family: var(--pn-mono);
}

.metadata-stats {
  display: flex; gap: var(--pn-space-5); margin-bottom: var(--pn-space-3);
  padding: var(--pn-space-3) 0;
  border-top: 1px solid var(--pn-border);
  border-bottom: 1px solid var(--pn-border);
}

.stat { display: flex; flex-direction: column; align-items: center; }
.stat-value {
  font-family: var(--pn-mono);
  font-size: 18px; font-weight: 700; color: var(--pn-text);
}
.stat-label {
  font-family: var(--pn-mono);
  font-size: 9px; color: var(--pn-text-muted);
  text-transform: uppercase; letter-spacing: 0.08em;
}

.metadata-sections { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
.sections-label { font-size: 11px; font-weight: 600; color: var(--pn-text-secondary); margin-right: 4px; }
.section-chip {
  font-size: 10px; padding: 1px 6px; border: 1px solid var(--pn-border);
  color: var(--pn-text-secondary);
}

/* ── Preflight ── */
.preflight-results {
  border: 1px solid var(--pn-border);
  padding: var(--pn-space-5); margin-bottom: var(--pn-space-4);
}

.preflight-header {
  font-family: var(--pn-mono);
  font-size: 10px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.08em; display: flex; align-items: center;
  gap: var(--pn-space-3); margin-bottom: var(--pn-space-2);
  color: var(--pn-text-secondary);
}

.preflight-badge {
  font-size: 10px; font-weight: 700; padding: 2px 8px;
  letter-spacing: 0.06em;
}
.preflight-badge.clear { background: var(--pn-ok-dim); color: var(--pn-ok); }
.preflight-badge.warnings { background: var(--pn-warn-dim); color: var(--pn-warn); }
.preflight-badge.blocked { background: var(--pn-err-dim); color: var(--pn-err); }

.preflight-meta { font-size: 11px; color: var(--pn-text-muted); margin-bottom: var(--pn-space-3); }

.check-row {
  display: flex; align-items: flex-start; gap: var(--pn-space-3);
  padding: var(--pn-space-2) 0; font-size: 12px;
  border-bottom: 1px solid var(--pn-border);
}
.check-row:last-child { border-bottom: none; }

.check-icon {
  font-family: var(--pn-mono);
  font-size: 9px; font-weight: 700; padding: 1px 5px;
  flex-shrink: 0; margin-top: 1px; letter-spacing: 0.04em;
}
.check-row.pass .check-icon { background: var(--pn-ok-dim); color: var(--pn-ok); }
.check-row.warning .check-icon { background: var(--pn-warn-dim); color: var(--pn-warn); }
.check-row.blocker .check-icon { background: var(--pn-err-dim); color: var(--pn-err); }
.check-row.info .check-icon { background: var(--pn-border); color: var(--pn-text-muted); }

.check-name { font-weight: 600; min-width: 90px; flex-shrink: 0; }
.check-detail { color: var(--pn-text-secondary); }

/* ── Action row ── */
.action-row { display: flex; gap: var(--pn-space-3); }

.action-btn {
  flex: 1; padding: var(--pn-space-4);
  font-family: var(--pn-mono);
  font-size: 12px; font-weight: 600;
  border: 1px solid var(--pn-border); background: var(--pn-surface);
  color: var(--pn-text-muted);
  cursor: not-allowed;
  transition: all var(--pn-duration) var(--pn-ease);
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.action-btn.ready { cursor: pointer; }
.action-btn:disabled { opacity: 0.4; }
.preflight-btn.ready { border-color: var(--pn-text-secondary); color: var(--pn-text); }
.preflight-btn.ready:hover { border-color: var(--pn-text); }
.launch-btn.ready {
  border-color: var(--pn-text);
  background: var(--pn-text);
  color: var(--pn-bg);
}
.launch-btn.ready:hover { background: #222; }

/* ── Instructions ── */
.instructions-section { margin-bottom: var(--pn-space-6); }
.instructions-input {
  width: 100%;
  padding: var(--pn-space-3) var(--pn-space-4);
  font-family: var(--pn-serif);
  font-size: 13px;
  line-height: 1.6;
  resize: vertical;
  min-height: 60px;
}
.instructions-input::placeholder { color: var(--pn-text-ghost); font-size: 12px; }
.instructions-hint { font-size: 10px; color: var(--pn-text-muted); margin-top: var(--pn-space-1); }

/* ── Active sessions ── */
.active-section {
  margin-top: var(--pn-space-6);
  padding-top: var(--pn-space-5);
  border-top: 1px solid var(--pn-border);
}
.active-row { border-color: var(--pn-warn) !important; }

.error-msg { color: var(--pn-err); font-size: 12px; margin-top: var(--pn-space-3); text-align: center; }

/* ── Session history ── */
.history-section {
  margin-top: var(--pn-space-7);
  padding-top: var(--pn-space-6);
  border-top: 1px solid var(--pn-border);
}

.history-header {
  font-family: var(--pn-mono);
  font-size: 10px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.08em; color: var(--pn-text-muted);
  margin-bottom: var(--pn-space-3);
}

.session-list { display: flex; flex-direction: column; gap: 2px; }

.session-row {
  display: flex; align-items: center; gap: var(--pn-space-3);
  padding: var(--pn-space-3) var(--pn-space-4);
  border: 1px solid transparent;
  cursor: pointer;
  transition: all var(--pn-duration) var(--pn-ease);
}
.session-row:hover {
  border-color: var(--pn-border);
  background: var(--pn-surface);
}

.session-status-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
.session-status-dot.completed { background: var(--pn-ok); }
.session-status-dot.running { background: var(--pn-warn); }
.session-status-dot.failed { background: var(--pn-err); }
.session-status-dot.created { background: var(--pn-border-strong); }
.session-status-dot.stopped { background: var(--pn-text-ghost); }

.session-id {
  font-size: 12px; font-weight: 600; color: var(--pn-text);
}

.session-date { font-size: 11px; color: var(--pn-text-muted); flex: 1; }

.session-badge {
  font-size: 9px; font-weight: 600; padding: 2px 6px;
  text-transform: uppercase; letter-spacing: 0.06em;
}
.session-badge.completed { background: var(--pn-ok-dim); color: var(--pn-ok); }
.session-badge.running { background: var(--pn-warn-dim); color: var(--pn-warn); }
.session-badge.failed { background: var(--pn-err-dim); color: var(--pn-err); }
.session-badge.created { border: 1px solid var(--pn-border); color: var(--pn-text-muted); }

/* ── Graph model grid ── */
.graph-model-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
  gap: var(--pn-space-3);
}

.graph-model-card {
  border: 1px solid var(--pn-border);
  padding: var(--pn-space-3) var(--pn-space-4);
}

.gmc-header { margin-bottom: var(--pn-space-2); }
.gmc-role {
  font-family: var(--pn-mono);
  font-size: 12px; font-weight: 700; color: var(--pn-text);
  text-transform: uppercase; letter-spacing: 0.04em;
}
.gmc-desc { font-size: 10px; color: var(--pn-text-muted); margin-top: 2px; }

/* ── Mode toggle ── */
.mode-toggle {
  display: flex;
  gap: 0;
  margin-bottom: var(--pn-space-3);
  border: 1px solid var(--pn-border);
}

.mode-btn {
  flex: 1;
  font-family: var(--pn-mono);
  font-size: 10px;
  font-weight: 600;
  padding: var(--pn-space-2) var(--pn-space-3);
  border: none;
  border-right: 1px solid var(--pn-border);
  background: transparent;
  color: var(--pn-text-muted);
  cursor: pointer;
  transition: all var(--pn-duration) var(--pn-ease);
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.mode-btn:last-child { border-right: none; }

.mode-btn.active {
  background: var(--pn-text);
  color: var(--pn-bg);
}
.mode-btn:hover:not(.active) { background: var(--pn-bg); color: var(--pn-text); }

/* ── Batch ── */
.batch-zone { min-height: 100px; }

.batch-file-list {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 2px;
  text-align: left;
}

.batch-file-item {
  display: flex;
  align-items: center;
  gap: var(--pn-space-2);
  padding: var(--pn-space-1) var(--pn-space-2);
  border: 1px solid var(--pn-border);
  font-size: 11px;
}
.batch-file-item .file-name { flex: 1; }

.file-icon.small {
  font-size: 8px;
  padding: 1px 4px;
  margin-bottom: 0;
}

.remove-btn {
  background: none;
  border: none;
  color: var(--pn-text-muted);
  cursor: pointer;
  font-size: 14px;
  padding: 0 2px;
  line-height: 1;
}
.remove-btn:hover { color: var(--pn-err); }

.batch-count {
  font-size: 10px;
  color: var(--pn-text-muted);
  margin-top: var(--pn-space-2);
}

.drop-hint {
  font-size: 10px;
  color: var(--pn-text-ghost);
  margin-top: var(--pn-space-1);
}

/* ── Import ── */
.import-stats {
  font-size: 11px;
  color: var(--pn-accent);
  margin-top: var(--pn-space-1);
  font-weight: 600;
}

.saved-graphs {
  margin-top: var(--pn-space-4);
  border: 1px solid var(--pn-border);
}

.saved-graphs-header {
  padding: var(--pn-space-2) var(--pn-space-3);
  border-bottom: 1px solid var(--pn-border);
  font-size: 10px;
  color: var(--pn-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.saved-graph-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--pn-space-3);
  padding: var(--pn-space-3);
  border-bottom: 1px solid var(--pn-border);
}

.saved-graph-row:last-child {
  border-bottom: 0;
}

.saved-graph-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.saved-graph-title {
  color: var(--pn-text);
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.saved-graph-meta {
  color: var(--pn-text-muted);
  font-size: 10px;
}

.saved-graph-btn {
  flex-shrink: 0;
  border: 1px solid var(--pn-border-strong);
  background: var(--pn-surface);
  color: var(--pn-text);
  padding: var(--pn-space-2) var(--pn-space-3);
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  cursor: pointer;
}

.saved-graph-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.saved-graph-btn:not(:disabled):hover {
  border-color: var(--pn-accent);
  color: var(--pn-accent);
}
</style>
