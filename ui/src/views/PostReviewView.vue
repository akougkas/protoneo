<template>
  <div class="post-review-view">
    <header class="post-header">
      <div class="header-left">
        <button class="brand-btn" @click="goSession">PROTONEO</button>
        <div class="title-stack">
          <span class="eyebrow">Post Review</span>
          <h1>{{ paperTitle || 'Final Review' }}</h1>
        </div>
      </div>
      <div class="header-actions">
        <span class="model-chip" v-if="chairModel">{{ shortModel(chairModel) }}</span>
        <button @click="saveReview" :disabled="saving || loading">
          {{ saving ? 'Saving...' : 'Save Review' }}
        </button>
        <button class="primary" @click="writeArtifacts" :disabled="writing || loading">
          {{ writing ? 'Writing...' : 'Write Artifacts' }}
        </button>
      </div>
    </header>

    <main class="post-shell">
      <section class="review-editor" aria-label="Final review editor">
        <div class="score-band">
          <div class="score-control">
            <label>Overall Merit</label>
            <select v-model.number="draft.overall_merit.score" @change="syncMeritLabel">
              <option :value="5">5 Strong accept</option>
              <option :value="4">4 Accept</option>
              <option :value="3">3 Borderline</option>
              <option :value="2">2 Weak reject</option>
              <option :value="1">1 Reject</option>
            </select>
          </div>
          <div class="score-control">
            <label>Expertise</label>
            <select v-model.number="draft.reviewer_expertise.score">
              <option :value="4">4 Expert</option>
              <option :value="3">3 Knowledgeable</option>
              <option :value="2">2 Some familiarity</option>
              <option :value="1">1 No familiarity</option>
            </select>
          </div>
          <div class="score-control wide">
            <label>Recommendation Rationale</label>
            <input v-model="draft.overall_merit.rationale" />
          </div>
        </div>

        <div class="dimension-grid">
          <div v-for="dim in dimensionDefs" :key="dim.key" class="dimension-row">
            <label>{{ dim.label }}</label>
            <select v-model.number="draft[dim.key].score">
              <option v-for="score in [5, 4, 3, 2, 1]" :key="score" :value="score">{{ score }}</option>
            </select>
            <input v-model="draft[dim.key].label" placeholder="label" />
            <input v-model="draft[dim.key].rationale" placeholder="rationale" />
          </div>
        </div>

        <div class="field-grid">
          <div v-for="field in fieldDefs" :key="field.key" class="review-field">
            <div class="field-label-row">
              <label>{{ field.label }}</label>
              <button @click="copyField(field.key)">{{ copiedField === field.key ? 'Copied' : 'Copy' }}</button>
            </div>
            <textarea v-model="draft[field.key]" :rows="field.rows"></textarea>
          </div>
        </div>

        <div class="best-paper-row">
          <label>
            <input type="checkbox" v-model="draft.best_paper_consideration.nominate" />
            Best Paper Consideration
          </label>
          <input v-model="draft.best_paper_consideration.rationale" />
        </div>
      </section>

      <aside class="chair-panel" aria-label="PC Chair">
        <div class="chair-head">
          <div>
            <span class="eyebrow">PC Chair</span>
            <h2>Post-review editor</h2>
          </div>
          <span :class="['status-pill', saveStatusClass]">{{ saveStatus }}</span>
        </div>

        <div class="metric-strip">
          <div class="metric">
            <span>Reviews</span>
            <strong>{{ metrics.reviewCount }}</strong>
          </div>
          <div class="metric">
            <span>Turns</span>
            <strong>{{ metrics.deliberationTurns }}</strong>
          </div>
          <div class="metric">
            <span>Nodes</span>
            <strong>{{ metrics.graphNodes }}</strong>
          </div>
          <div class="metric">
            <span>Edges</span>
            <strong>{{ metrics.graphEdges }}</strong>
          </div>
        </div>

        <div class="chat-log">
          <div v-if="!history.length" class="empty-state">
            The chair has the manuscript, graph, reviews, deliberation, and final draft.
          </div>
          <div v-for="(turn, idx) in history" :key="idx" class="chat-turn">
            <div class="user-msg">{{ turn.user }}</div>
            <div class="chair-msg">{{ turn.reply }}</div>
            <ul v-if="turn.edit_summary && turn.edit_summary.length" class="edit-summary">
              <li v-for="(item, itemIdx) in turn.edit_summary" :key="itemIdx">{{ item }}</li>
            </ul>
          </div>
        </div>

        <div class="chat-compose">
          <textarea
            v-model="message"
            rows="5"
            @keydown.enter.ctrl.prevent="sendChairMessage"
          ></textarea>
          <div class="compose-actions">
            <label class="apply-toggle">
              <input type="checkbox" v-model="applyChairEdits" />
              Apply edits
            </label>
            <button class="primary" @click="sendChairMessage" :disabled="chatting || !message.trim()">
              {{ chatting ? 'Thinking...' : 'Send' }}
            </button>
          </div>
        </div>

        <div v-if="artifactMessage" class="artifact-note">{{ artifactMessage }}</div>
      </aside>
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  getPcChairChat,
  getReviewPacket,
  getSession,
  pcChairChat,
  updateFinalReview,
  writeReviewArtifacts,
} from '../api/kernel.js'

const route = useRoute()
const router = useRouter()
const sessionId = computed(() => route.params.sessionId)

const loading = ref(true)
const saving = ref(false)
const writing = ref(false)
const chatting = ref(false)
const paperTitle = ref('')
const chairModel = ref('')
const message = ref('')
const history = ref([])
const copiedField = ref('')
const lastSaved = ref('')
const artifactMessage = ref('')
const applyChairEdits = ref(true)

const metrics = reactive({
  reviewCount: 0,
  deliberationTurns: 0,
  graphNodes: 0,
  graphEdges: 0,
})

const meritLabels = {
  5: 'Strong accept',
  4: 'Accept',
  3: 'Borderline',
  2: 'Weak reject',
  1: 'Reject',
}

const dimensionDefs = [
  { key: 'relevance', label: 'Relevance' },
  { key: 'technical_soundness', label: 'Soundness' },
  { key: 'technical_importance', label: 'Importance' },
  { key: 'originality', label: 'Originality' },
  { key: 'quality_of_presentation', label: 'Presentation' },
  { key: 'recommended_action', label: 'Action' },
  { key: 'level_of_confidence', label: 'Confidence' },
  { key: 'level_of_expertise', label: 'Expertise Level' },
]

const fieldDefs = [
  { key: 'paper_summary', label: 'Paper Summary', rows: 5 },
  { key: 'strengths', label: 'Strengths', rows: 7 },
  { key: 'weaknesses', label: 'Weaknesses', rows: 7 },
  { key: 'comments_for_rebuttal', label: 'Comments For Rebuttal', rows: 4 },
  { key: 'detailed_comments_for_authors', label: 'Detailed Comments For Authors', rows: 9 },
  { key: 'comments_for_authors', label: 'Comments For Authors', rows: 8 },
  { key: 'questions_for_authors', label: 'Questions For Authors', rows: 4 },
  { key: 'revision_actions', label: 'Revision Actions', rows: 5 },
  { key: 'comments_for_pc', label: 'Comments For PC', rows: 5 },
  { key: 'reproducibility_committee_focus', label: 'Reproducibility Focus', rows: 3 },
]

const draft = reactive(defaultReview())

const saveStatus = computed(() => {
  if (saving.value) return 'saving'
  if (lastSaved.value) return `saved ${lastSaved.value}`
  return 'draft'
})

const saveStatusClass = computed(() => {
  if (saving.value) return 'busy'
  if (lastSaved.value) return 'saved'
  return 'draft'
})

function defaultDimension(score = 3, label = 'MODERATE') {
  return { score, label, rationale: '', reason: '' }
}

function defaultReview() {
  return {
    overall_merit: { score: 3, label: 'Borderline', rationale: '' },
    reviewer_expertise: { score: 3, label: 'Knowledgeable', reason: '' },
    paper_summary: '',
    strengths: '',
    weaknesses: '',
    comments_for_rebuttal: '',
    detailed_comments_for_authors: '',
    comments_for_authors: '',
    comments_for_pc: '',
    internal_committee_concerns: '',
    questions_for_authors: '',
    revision_actions: '',
    relevance: defaultDimension(4, 'HIGH'),
    technical_soundness: defaultDimension(),
    technical_importance: defaultDimension(),
    originality: defaultDimension(),
    quality_of_presentation: defaultDimension(),
    recommended_action: defaultDimension(3, 'WEAK REJECT'),
    level_of_confidence: defaultDimension(4, 'HIGH'),
    level_of_expertise: defaultDimension(4, 'HIGH'),
    best_paper_consideration: { nominate: false, rationale: '' },
    reproducibility_committee_focus: '',
    submission_readiness: { status: 'revise_before_submit', reason: '' },
    linklings_offline_review_text: '',
    offline_review_path: '',
  }
}

function deepCopy(value) {
  return JSON.parse(JSON.stringify(value ?? null))
}

function formatItem(value) {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  if (typeof value !== 'object') return String(value)
  if (Array.isArray(value)) return value.map(formatItem).filter(Boolean).join('\n')
  const primary = value.point || value.action || value.question || value.concern ||
    value.issue || value.claim || value.text || value.description || value.summary || ''
  const details = ['evidence', 'target_section', 'why_it_matters', 'expected_review_impact', 'rationale', 'reason']
    .filter(k => value[k])
    .map(k => `${k.replace(/_/g, ' ')}: ${formatItem(value[k])}`)
  return [primary, ...details].filter(Boolean).join(' | ')
}

function normalizeReview(input) {
  const next = defaultReview()
  const source = input && typeof input === 'object' ? input : {}
  if (source.overall_merit) next.overall_merit = { ...next.overall_merit, ...source.overall_merit }
  if (source.final_recommendation && !source.overall_merit) {
    next.overall_merit = { ...next.overall_merit, ...source.final_recommendation }
  }
  if (source.reviewer_expertise || source.expertise) {
    next.reviewer_expertise = { ...next.reviewer_expertise, ...(source.reviewer_expertise || source.expertise) }
  }
  for (const dim of dimensionDefs) {
    if (source[dim.key] && typeof source[dim.key] === 'object') {
      next[dim.key] = { ...next[dim.key], ...source[dim.key] }
    }
  }
  for (const field of fieldDefs) {
    if (source[field.key] !== undefined) next[field.key] = formatItem(source[field.key])
  }
  if (source.best_paper_consideration && typeof source.best_paper_consideration === 'object') {
    next.best_paper_consideration = {
      ...next.best_paper_consideration,
      ...source.best_paper_consideration,
    }
  }
  if (source.submission_readiness && typeof source.submission_readiness === 'object') {
    next.submission_readiness = { ...next.submission_readiness, ...source.submission_readiness }
  }
  next.linklings_offline_review_text = source.linklings_offline_review_text || ''
  next.offline_review_path = source.offline_review_path || ''
  return next
}

function applyReview(review) {
  const next = normalizeReview(review)
  for (const [key, value] of Object.entries(next)) {
    draft[key] = deepCopy(value)
  }
}

function currentReviewPayload() {
  const out = deepCopy(draft)
  out.final_recommendation = { ...out.overall_merit }
  return out
}

function syncMeritLabel() {
  draft.overall_merit.label = meritLabels[draft.overall_merit.score] || draft.overall_merit.label
}

function shortModel(model) {
  if (!model) return ''
  const parts = model.split('/')
  return parts[parts.length - 1].slice(0, 34)
}

function goSession() {
  router.push({ name: 'Session', params: { sessionId: sessionId.value } })
}

async function load() {
  loading.value = true
  try {
    const [sessionRes, packetRes, chatRes] = await Promise.allSettled([
      getSession(sessionId.value),
      getReviewPacket(sessionId.value),
      getPcChairChat(sessionId.value),
    ])

    if (sessionRes.status === 'fulfilled') {
      const session = sessionRes.value.data || {}
      const meta = session.config?.metadata || session.metadata || {}
      paperTitle.value = meta.paper_title || meta.filename || ''
      const agents = session.config?.agents || {}
      chairModel.value = agents.meta?.model || Object.values(agents).find(v => v?.model)?.model || ''
    }

    if (packetRes.status === 'fulfilled') {
      const packet = packetRes.value.data || {}
      paperTitle.value = packet.paper_title || paperTitle.value
      metrics.reviewCount = packet.reviews?.length || 0
      metrics.deliberationTurns = (packet.deliberation || [])
        .reduce((sum, round) => sum + (round.entries?.length || 0), 0)
      metrics.graphNodes = packet.graph_node_count || 0
      metrics.graphEdges = packet.graph_edge_count || 0
      applyReview(packet.final_review || packet.pc_chair_review || {})
    }

    if (chatRes.status === 'fulfilled') {
      history.value = chatRes.value.data?.history || []
      if (chatRes.value.data?.final_review) applyReview(chatRes.value.data.final_review)
    }
  } finally {
    loading.value = false
  }
}

async function saveReview() {
  saving.value = true
  try {
    await updateFinalReview(sessionId.value, currentReviewPayload())
    lastSaved.value = new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
  } finally {
    saving.value = false
  }
}

async function sendChairMessage() {
  const text = message.value.trim()
  if (!text || chatting.value) return
  chatting.value = true
  try {
    const res = await pcChairChat(sessionId.value, {
      message: text,
      currentReview: currentReviewPayload(),
      applyEdits: applyChairEdits.value,
    })
    message.value = ''
    history.value = res.data?.history || [
      ...history.value,
      { user: text, reply: res.data?.reply || '', edit_summary: res.data?.edit_summary || [] },
    ]
    if (res.data?.final_review) {
      applyReview(res.data.final_review)
      if (res.data.applied_edits) {
        lastSaved.value = new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
      }
    }
    chairModel.value = res.data?.model || chairModel.value
  } finally {
    chatting.value = false
  }
}

async function writeArtifacts() {
  writing.value = true
  artifactMessage.value = ''
  try {
    await saveReview()
    const res = await writeReviewArtifacts(sessionId.value)
    const manifest = res.data?.manifest || {}
    const paths = manifest.artifact_paths || {}
    const offlinePath = Object.values(paths).find(p => String(p).endsWith('_protoneo_offline_review.txt'))
    artifactMessage.value = offlinePath || res.data?.output_dir || 'Artifacts written'
  } finally {
    writing.value = false
  }
}

async function copyField(key) {
  try {
    await navigator.clipboard.writeText(draft[key] || '')
    copiedField.value = key
    setTimeout(() => { copiedField.value = '' }, 1200)
  } catch {
    copiedField.value = ''
  }
}

onMounted(load)
</script>

<style scoped>
.post-review-view {
  min-height: 100vh;
  background: var(--pn-bg);
  color: var(--pn-text);
  display: flex;
  flex-direction: column;
}

.post-header {
  height: 64px;
  border-bottom: 1px solid var(--pn-border);
  background: var(--pn-surface);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 var(--pn-space-5);
  gap: var(--pn-space-5);
}

.header-left,
.header-actions {
  display: flex;
  align-items: center;
  gap: var(--pn-space-3);
  min-width: 0;
}

.brand-btn {
  border: none;
  background: transparent;
  padding: 0;
  font-size: 13px;
  font-weight: 800;
  letter-spacing: 0.18em;
}

.title-stack {
  min-width: 0;
}

.eyebrow {
  display: block;
  color: var(--pn-text-muted);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

h1 {
  max-width: 54vw;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 19px;
  margin-top: 2px;
}

.model-chip,
.status-pill {
  border: 1px solid var(--pn-border);
  background: var(--pn-bg);
  color: var(--pn-text-secondary);
  padding: 5px 9px;
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
}

.status-pill.saved {
  border-color: var(--pn-ok);
  color: var(--pn-ok);
}

.status-pill.busy {
  border-color: var(--pn-warn);
  color: var(--pn-warn);
}

button.primary {
  background: var(--pn-text);
  border-color: var(--pn-text);
  color: var(--pn-bg);
  font-weight: 700;
}

button.primary:hover:not(:disabled) {
  background: var(--pn-accent);
  border-color: var(--pn-accent);
}

.post-shell {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 420px;
}

.review-editor {
  min-width: 0;
  overflow: auto;
  padding: var(--pn-space-5);
}

.chair-panel {
  border-left: 1px solid var(--pn-border);
  background: var(--pn-surface);
  min-height: 0;
  display: grid;
  grid-template-rows: auto auto minmax(0, 1fr) auto auto;
}

.chair-head {
  padding: var(--pn-space-5);
  border-bottom: 1px solid var(--pn-border);
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--pn-space-3);
}

.chair-head h2 {
  font-size: 18px;
  margin-top: 2px;
}

.score-band,
.dimension-grid,
.best-paper-row {
  border: 1px solid var(--pn-border);
  background: var(--pn-surface);
}

.score-band {
  display: grid;
  grid-template-columns: 190px 190px minmax(220px, 1fr);
  gap: var(--pn-space-3);
  padding: var(--pn-space-4);
}

.score-control {
  display: flex;
  flex-direction: column;
  gap: var(--pn-space-2);
}

.score-control label,
.dimension-row label,
.review-field label {
  font-size: 10px;
  font-weight: 800;
  color: var(--pn-text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.07em;
}

.score-control select,
.score-control input {
  width: 100%;
  min-height: 34px;
}

.dimension-grid {
  margin-top: var(--pn-space-4);
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.dimension-row {
  display: grid;
  grid-template-columns: 120px 58px 112px minmax(0, 1fr);
  gap: var(--pn-space-2);
  align-items: center;
  padding: var(--pn-space-3);
  border-bottom: 1px solid var(--pn-border);
}

.dimension-row:nth-child(odd) {
  border-right: 1px solid var(--pn-border);
}

.dimension-row:nth-last-child(-n + 2) {
  border-bottom: none;
}

.dimension-row input,
.dimension-row select {
  width: 100%;
  min-width: 0;
}

.field-grid {
  margin-top: var(--pn-space-4);
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--pn-space-4);
}

.review-field {
  min-width: 0;
}

.review-field:nth-child(6),
.review-field:nth-child(7),
.review-field:nth-child(9) {
  grid-column: 1 / -1;
}

.field-label-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--pn-space-2);
  margin-bottom: var(--pn-space-2);
}

.field-label-row button {
  padding: 3px 8px;
  font-size: 10px;
}

textarea {
  width: 100%;
  resize: vertical;
  min-height: 96px;
  line-height: 1.55;
  background: var(--pn-surface);
}

.best-paper-row {
  margin-top: var(--pn-space-4);
  display: grid;
  grid-template-columns: 230px minmax(0, 1fr);
  gap: var(--pn-space-3);
  padding: var(--pn-space-4);
  align-items: center;
}

.best-paper-row label,
.apply-toggle {
  display: flex;
  align-items: center;
  gap: var(--pn-space-2);
  font-size: 11px;
  font-weight: 700;
  color: var(--pn-text-secondary);
  text-transform: uppercase;
}

.metric-strip {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  border-bottom: 1px solid var(--pn-border);
}

.metric {
  padding: var(--pn-space-3);
  border-right: 1px solid var(--pn-border);
}

.metric:last-child {
  border-right: none;
}

.metric span {
  display: block;
  color: var(--pn-text-muted);
  font-size: 9px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.07em;
}

.metric strong {
  display: block;
  font-size: 18px;
  margin-top: 2px;
}

.chat-log {
  overflow: auto;
  padding: var(--pn-space-4);
  display: flex;
  flex-direction: column;
  gap: var(--pn-space-4);
}

.empty-state {
  color: var(--pn-text-secondary);
  font-family: var(--pn-serif);
  font-size: 14px;
  line-height: 1.5;
}

.chat-turn {
  display: flex;
  flex-direction: column;
  gap: var(--pn-space-2);
}

.user-msg,
.chair-msg {
  border: 1px solid var(--pn-border);
  padding: var(--pn-space-3);
  line-height: 1.55;
  white-space: pre-wrap;
}

.user-msg {
  background: var(--pn-bg);
  margin-left: var(--pn-space-5);
}

.chair-msg {
  background: #f4fbfa;
  border-color: var(--pn-accent-dim);
  margin-right: var(--pn-space-5);
  font-family: var(--pn-serif);
  font-size: 14px;
}

.edit-summary {
  margin-left: var(--pn-space-5);
  padding-left: var(--pn-space-4);
  color: var(--pn-text-secondary);
  font-size: 11px;
}

.chat-compose {
  border-top: 1px solid var(--pn-border);
  padding: var(--pn-space-4);
}

.chat-compose textarea {
  min-height: 110px;
}

.compose-actions {
  margin-top: var(--pn-space-3);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--pn-space-3);
}

.artifact-note {
  border-top: 1px solid var(--pn-border);
  padding: var(--pn-space-3) var(--pn-space-4);
  color: var(--pn-ok);
  font-size: 11px;
  overflow-wrap: anywhere;
}

@media (max-width: 1120px) {
  .post-shell {
    grid-template-columns: 1fr;
  }

  .chair-panel {
    border-left: none;
    border-top: 1px solid var(--pn-border);
    min-height: 620px;
  }

  h1 {
    max-width: 46vw;
  }
}

@media (max-width: 760px) {
  .post-header {
    height: auto;
    align-items: flex-start;
    flex-direction: column;
    padding: var(--pn-space-4);
  }

  .header-actions {
    width: 100%;
    flex-wrap: wrap;
  }

  h1 {
    max-width: 100%;
    white-space: normal;
  }

  .score-band,
  .dimension-grid,
  .field-grid,
  .best-paper-row {
    grid-template-columns: 1fr;
  }

  .dimension-row,
  .dimension-row:nth-child(odd) {
    grid-template-columns: 1fr;
    border-right: none;
  }

  .review-field,
  .review-field:nth-child(6),
  .review-field:nth-child(7),
  .review-field:nth-child(9) {
    grid-column: auto;
  }
}
</style>
