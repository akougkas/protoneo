<template>
  <section class="final-review">
    <div class="fr-header">
      <div>
        <span class="fr-kicker">Official Draft</span>
        <h2>Final Review</h2>
      </div>
      <div class="fr-actions">
        <span v-if="chairModel" class="fr-model">{{ shortModel(chairModel) }}</span>
        <button class="fr-ask" @click="askWholeReview">Ask PC Chair</button>
        <button class="fr-edit" @click="editing = !editing">
          {{ editing ? 'Read Review' : 'Edit Review' }}
        </button>
      </div>
    </div>

    <div v-if="!editing" class="read-mode">
      <div class="decision-strip">
        <div class="decision-card">
          <span>Overall Merit</span>
          <strong>{{ scoreValue(fields.overall_merit) }}</strong>
          <small>{{ scoreLabel(fields.overall_merit) }}</small>
        </div>
        <div class="decision-card">
          <span>Expertise</span>
          <strong>{{ scoreValue(fields.reviewer_expertise) }}</strong>
          <small>{{ scoreLabel(fields.reviewer_expertise) }}</small>
        </div>
        <div v-if="rawReview.final_recommendation" class="decision-card">
          <span>Recommendation</span>
          <strong>{{ scoreValue(rawReview.final_recommendation) }}</strong>
          <small>{{ scoreLabel(rawReview.final_recommendation) }}</small>
        </div>
        <div v-if="rawReview.level_of_confidence || rawReview.confidence" class="decision-card">
          <span>Confidence</span>
          <strong>{{ scoreValue(rawReview.level_of_confidence || rawReview.confidence) }}</strong>
          <small>{{ scoreLabel(rawReview.level_of_confidence || rawReview.confidence) }}</small>
        </div>
      </div>

      <div class="read-fields">
        <article
          v-for="f in readableFieldDefs"
          :key="f.key"
          v-show="hasFieldContent(f.key)"
          class="read-field"
        >
          <div class="read-field-head">
            <div>
              <span v-if="f.private" class="private-chip">PC only</span>
              <h3>{{ f.label }}</h3>
            </div>
            <button class="field-ask" @click="askField(f.key, f.label)">Ask about this</button>
          </div>
          <ul v-if="Array.isArray(rawReview[f.key])" class="read-list">
            <li v-for="(item, i) in rawReview[f.key]" :key="f.key + '-' + i">
              {{ formatReviewItem(item) }}
            </li>
          </ul>
          <div v-else class="read-prose" v-html="md(formatEditorText(rawReview[f.key] ?? fields[f.key]))"></div>
        </article>
      </div>

      <div v-if="isDirty" class="save-bar read-save">
        <span class="dirty-note">Unsaved draft edits</span>
        <button @click="saveReview" class="save-btn" :disabled="saving">
          {{ saving ? 'Saving...' : 'Save Final Review' }}
        </button>
      </div>
      <div v-else-if="lastSaved" class="save-bar read-save">
        <span class="save-status">Saved {{ lastSaved }}</span>
      </div>
    </div>

    <div v-else class="edit-mode">
      <div class="score-row">
        <div class="score-field">
          <label>Overall Merit</label>
          <select v-model.number="fields.overall_merit.score" @change="onScoreChange">
            <option :value="5">5 Strong accept</option>
            <option :value="4">4 Accept</option>
            <option :value="3">3 Borderline</option>
            <option :value="2">2 Weak reject</option>
            <option :value="1">1 Reject</option>
          </select>
        </div>
        <div class="score-field">
          <label>Reviewer Expertise</label>
          <select v-model.number="fields.reviewer_expertise.score" @change="markDirty">
            <option :value="4">4 Expert</option>
            <option :value="3">3 Knowledgeable</option>
            <option :value="2">2 Some familiarity</option>
            <option :value="1">1 No familiarity</option>
          </select>
        </div>
        <div v-if="lightpassLoading" class="lightpass-indicator">
          <span class="lp-dot"></span> Checking alignment...
        </div>
      </div>

      <div v-if="Object.keys(lightpassSuggestions).length > 0" class="lightpass-banner">
        <div class="lp-title">Score changed. Suggested edits</div>
        <div v-for="(text, key) in lightpassSuggestions" :key="key" class="lp-suggestion">
          <div class="lp-field-name">{{ fieldLabels[key] || key }}</div>
          <div class="lp-preview">{{ String(text).substring(0, 220) }}{{ String(text).length > 220 ? '...' : '' }}</div>
          <div class="lp-actions">
            <button @click="acceptSuggestion(key, text)" class="lp-btn accept">Accept</button>
            <button @click="rejectSuggestion(key)" class="lp-btn reject">Dismiss</button>
          </div>
        </div>
      </div>

      <div v-for="f in editableFieldDefs" :key="f.key" class="review-field-block">
        <div class="field-header">
          <label>{{ f.label }}</label>
          <span v-if="f.private" class="field-hint">hidden from authors</span>
          <div class="field-actions">
            <button
              @click="askField(f.key, f.label)"
              class="field-btn"
              type="button"
            >Ask</button>
            <button
              @click="toggleRefine(f.key)"
              :class="['field-btn', { active: refiningField === f.key }]"
              :disabled="refiningField && refiningField !== f.key"
              type="button"
            >Refine</button>
            <button @click="copyField(f.key)" class="field-btn" type="button">
              {{ copiedField === f.key ? 'Copied' : 'Copy' }}
            </button>
          </div>
        </div>

        <textarea
          v-model="fields[f.key]"
          :class="['field-textarea', { streaming: refiningField === f.key && refineStreaming }]"
          :rows="f.rows"
          @input="markDirty"
        ></textarea>

        <div v-if="refiningField === f.key" class="refine-input-bar">
          <input
            v-model="refineInstruction"
            @keydown.enter.prevent="sendRefine(f.key)"
            placeholder="Specific edit instruction"
            :disabled="refineStreaming"
            class="refine-input"
          />
          <button @click="sendRefine(f.key)" :disabled="refineStreaming || !refineInstruction.trim()" class="refine-send">
            {{ refineStreaming ? 'Streaming...' : 'Send' }}
          </button>
          <button @click="cancelRefine" class="refine-cancel">Cancel</button>
        </div>
      </div>

      <div class="save-bar">
        <button v-if="isDirty" @click="saveReview" class="save-btn" :disabled="saving">
          {{ saving ? 'Saving...' : 'Save Changes' }}
        </button>
        <span v-if="lastSaved" class="save-status">Saved {{ lastSaved }}</span>
      </div>
    </div>
  </section>
</template>

<script setup>
import { ref, reactive, watch } from 'vue'
import { refineField as apiRefineField, scoreLightpass, updateFinalReview } from '../api/kernel.js'
import { renderMarkdown } from '../utils/markdown.js'

const md = renderMarkdown

const props = defineProps({
  sessionId: { type: String, required: true },
  initialReview: { type: Object, default: () => ({}) },
  chairModel: { type: String, default: '' },
})

const emit = defineEmits(['review-updated', 'ask-chair', 'dirty-changed'])

const fieldLabels = {
  panel_summary: 'Panel Summary',
  author_facing_summary: 'Author-Facing Summary',
  paper_summary: 'Paper Summary',
  strengths: 'Strengths',
  weaknesses: 'Weaknesses',
  questions_for_authors: 'Questions for Authors',
  revision_actions: 'Revision Actions',
  prioritized_revision_plan: 'Prioritized Revision Plan',
  comments_for_rebuttal: 'Comments for Rebuttal',
  detailed_comments_for_authors: 'Detailed Comments for Authors',
  comments_for_authors: 'Comments for Authors',
  comments_for_pc: 'Comments for PC',
  internal_committee_concerns: 'Internal Committee Concerns',
  decision_risk_notes: 'Decision Risk Notes',
  reproducibility_committee_focus: 'Reproducibility Committee Focus',
}

const readableFieldDefs = [
  { key: 'panel_summary', label: 'Panel Summary' },
  { key: 'author_facing_summary', label: 'Author-Facing Summary' },
  { key: 'paper_summary', label: 'Paper Summary' },
  { key: 'strengths', label: 'Strengths' },
  { key: 'weaknesses', label: 'Weaknesses' },
  { key: 'questions_for_authors', label: 'Questions for Authors' },
  { key: 'revision_actions', label: 'Revision Actions' },
  { key: 'prioritized_revision_plan', label: 'Prioritized Revision Plan' },
  { key: 'comments_for_rebuttal', label: 'Comments for Rebuttal' },
  { key: 'detailed_comments_for_authors', label: 'Detailed Comments for Authors' },
  { key: 'comments_for_authors', label: 'Comments for Authors' },
  { key: 'reproducibility_committee_focus', label: 'Reproducibility Committee Focus' },
  { key: 'comments_for_pc', label: 'Comments for PC', private: true },
  { key: 'internal_committee_concerns', label: 'Internal Committee Concerns', private: true },
  { key: 'decision_risk_notes', label: 'Decision Risk Notes', private: true },
]

const editableFieldDefs = [
  { key: 'paper_summary', label: 'Paper Summary', rows: 5 },
  { key: 'strengths', label: 'Strengths', rows: 6 },
  { key: 'weaknesses', label: 'Weaknesses', rows: 6 },
  { key: 'comments_for_authors', label: 'Comments for Authors', rows: 10 },
  { key: 'comments_for_rebuttal', label: 'Comments for Rebuttal', rows: 6 },
  { key: 'detailed_comments_for_authors', label: 'Detailed Comments for Authors', rows: 8 },
  { key: 'comments_for_pc', label: 'Comments for PC', rows: 5, private: true },
]

const meritLabels = { 5: 'Strong accept', 4: 'Accept', 3: 'Borderline', 2: 'Weak reject', 1: 'Reject' }
const expertiseLabels = { 4: 'Expert', 3: 'Knowledgeable', 2: 'Some familiarity', 1: 'No familiarity' }

const rawReview = ref({})
const fields = reactive({
  overall_merit: { score: 3, label: 'Borderline' },
  reviewer_expertise: { score: 3, label: 'Knowledgeable' },
  paper_summary: '',
  strengths: '',
  weaknesses: '',
  comments_for_authors: '',
  comments_for_pc: '',
  comments_for_rebuttal: '',
  detailed_comments_for_authors: '',
})

const editing = ref(false)
const refiningField = ref(null)
const refineInstruction = ref('')
const refineStreaming = ref(false)
const copiedField = ref(null)
const isDirty = ref(false)
const saving = ref(false)
const lastSaved = ref('')
const lightpassLoading = ref(false)
const lightpassSuggestions = ref({})

function deepCopy(value) {
  return JSON.parse(JSON.stringify(value || {}))
}

function shortModel(m) {
  if (!m) return ''
  const parts = m.split('/')
  return parts[parts.length - 1].slice(0, 28)
}

function scoreValue(value) {
  if (!value || typeof value !== 'object') return '-'
  return value.score ?? value.value ?? '-'
}

function scoreLabel(value) {
  if (!value || typeof value !== 'object') return ''
  return value.label || value.recommendation || value.status || value.reason || value.rationale || ''
}

function formatReviewItem(value) {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  if (typeof value !== 'object') return String(value)
  if (Array.isArray(value)) return value.map(formatReviewItem).filter(Boolean).join('\n')

  const primary = value.point || value.action || value.question || value.concern ||
    value.issue || value.claim || value.text || value.description ||
    value.summary || value.recommendation || value.rationale || ''
  const tags = ['severity', 'importance', 'priority', 'fixability']
    .filter(k => value[k])
    .map(k => `${k.replace(/_/g, ' ')}: ${formatReviewItem(value[k])}`)
  const details = [
    ['evidence', 'Evidence'],
    ['target_section', 'Target'],
    ['why_it_matters', 'Why it matters'],
    ['expected_review_impact', 'Expected impact'],
    ['your_resolution', 'Resolution'],
    ['why_reviewers_disagree', 'Why reviewers disagree'],
  ]
    .filter(([k]) => value[k])
    .map(([k, label]) => `${label}: ${formatReviewItem(value[k])}`)

  let text = primary || Object.entries(value)
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([k, v]) => `${k.replace(/_/g, ' ')}: ${formatReviewItem(v)}`)
    .join('; ')
  if (tags.length) text += ` [${tags.join('; ')}]`
  if (details.length) text += ` - ${details.join(' ')}`
  return text.trim()
}

function formatEditorText(value) {
  if (Array.isArray(value)) return value.map(formatReviewItem).filter(Boolean).join('\n')
  return formatReviewItem(value)
}

function hasFieldContent(key) {
  const value = rawReview.value[key]
  if (Array.isArray(value)) return value.length > 0
  if (value && typeof value === 'object') return Object.values(value).some(v => v)
  return Boolean(String(value ?? fields[key] ?? '').trim())
}

function loadReview(data, { dirty = false } = {}) {
  if (!data || typeof data !== 'object') return
  rawReview.value = deepCopy(data)
  if (data.overall_merit) fields.overall_merit = { ...fields.overall_merit, ...data.overall_merit }
  if (data.reviewer_expertise) fields.reviewer_expertise = { ...fields.reviewer_expertise, ...data.reviewer_expertise }
  for (const key of Object.keys(fieldLabels)) {
    if (data[key] !== undefined && key in fields) fields[key] = formatEditorText(data[key])
  }
  isDirty.value = dirty
  emit('dirty-changed', dirty)
}

loadReview(props.initialReview)

watch(() => props.initialReview, (v) => loadReview(v), { deep: true })

function markDirty() {
  isDirty.value = true
  emit('dirty-changed', true)
}

function getTextFields() {
  const out = {}
  for (const f of editableFieldDefs) {
    out[f.key] = fields[f.key]
  }
  return out
}

function getAllFields() {
  return {
    ...deepCopy(rawReview.value),
    overall_merit: { ...fields.overall_merit },
    reviewer_expertise: { ...fields.reviewer_expertise },
    ...getTextFields(),
  }
}

async function onScoreChange() {
  const score = fields.overall_merit.score
  fields.overall_merit.label = meritLabels[score] || ''
  markDirty()

  lightpassLoading.value = true
  lightpassSuggestions.value = {}
  try {
    const res = await scoreLightpass(
      props.sessionId, score, fields.overall_merit.label, getTextFields()
    )
    if (res.data?.suggestions && Object.keys(res.data.suggestions).length > 0) {
      lightpassSuggestions.value = res.data.suggestions
    }
  } catch (e) {
    console.error('Lightpass failed:', e)
  } finally {
    lightpassLoading.value = false
  }
}

function acceptSuggestion(key, text) {
  if (key in fields) fields[key] = text
  rawReview.value[key] = text
  const remaining = { ...lightpassSuggestions.value }
  delete remaining[key]
  lightpassSuggestions.value = remaining
  markDirty()
}

function rejectSuggestion(key) {
  const remaining = { ...lightpassSuggestions.value }
  delete remaining[key]
  lightpassSuggestions.value = remaining
}

function toggleRefine(key) {
  if (refiningField.value === key) {
    refiningField.value = null
    refineInstruction.value = ''
  } else {
    refiningField.value = key
    refineInstruction.value = ''
  }
}

function cancelRefine() {
  refiningField.value = null
  refineInstruction.value = ''
  refineStreaming.value = false
}

async function sendRefine(key) {
  if (!refineInstruction.value.trim() || refineStreaming.value) return
  refineStreaming.value = true
  fields[key] = ''

  try {
    await apiRefineField(props.sessionId, key, refineInstruction.value, getTextFields())
  } catch (e) {
    fields[key] = `Error: ${e.message || 'Refine failed'}`
    refineStreaming.value = false
  }
}

function handleRefineToken(field, chunk) {
  if (field === refiningField.value) {
    fields[field] += chunk
  }
}

function handleRefineDone(field, content) {
  if (field === refiningField.value) {
    fields[field] = content
    rawReview.value[field] = content
    refineStreaming.value = false
    refineInstruction.value = ''
    markDirty()
  }
}

function handleRefineError(field, detail) {
  if (field === refiningField.value) {
    fields[field] = `Error: ${detail}`
    refineStreaming.value = false
  }
}

async function copyField(key) {
  try {
    await navigator.clipboard.writeText(fields[key] || formatEditorText(rawReview.value[key]) || '')
    copiedField.value = key
    setTimeout(() => { copiedField.value = null }, 1500)
  } catch (e) {
    console.error('Copy failed:', e)
  }
}

function mergeReviewPatch(base, patch) {
  const merged = deepCopy(base)
  for (const [key, value] of Object.entries(patch || {})) {
    if (value === null || value === undefined) continue
    if (
      typeof value === 'object'
      && !Array.isArray(value)
      && typeof merged[key] === 'object'
      && !Array.isArray(merged[key])
    ) {
      merged[key] = { ...merged[key], ...value }
    } else {
      merged[key] = value
    }
  }
  return merged
}

function applyReviewPatch(patch) {
  const merged = mergeReviewPatch(getAllFields(), patch)
  loadReview(merged, { dirty: true })
}

async function saveReview() {
  saving.value = true
  try {
    const payload = getAllFields()
    await updateFinalReview(props.sessionId, payload)
    rawReview.value = deepCopy(payload)
    isDirty.value = false
    emit('dirty-changed', false)
    lastSaved.value = new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
    emit('review-updated', payload)
    return true
  } catch (e) {
    console.error('Save failed:', e)
    return false
  } finally {
    saving.value = false
  }
}

function excerptForKey(key) {
  return formatEditorText(rawReview.value[key] ?? fields[key]).slice(0, 2400)
}

function askField(key, label) {
  emit('ask-chair', {
    id: `final:${key}`,
    type: 'final_review_field',
    label,
    summary: label,
    excerpt: excerptForKey(key),
    metadata: { field: key, source: 'final_review' },
  })
}

function askWholeReview() {
  emit('ask-chair', {
    id: 'final-review',
    type: 'final_review',
    label: 'Final Review',
    summary: 'Official structured final review draft',
    excerpt: JSON.stringify(getAllFields(), null, 2).slice(0, 3600),
    metadata: { source: 'final_review' },
  })
}

defineExpose({
  handleRefineToken,
  handleRefineDone,
  handleRefineError,
  loadReview,
  applyReviewPatch,
  saveReview,
  getAllFields,
})
</script>

<style scoped>
.final-review {
  border: 1px solid var(--pn-border);
  border-top: 2px solid var(--pn-text);
  padding: var(--pn-space-5);
  margin-bottom: var(--pn-space-6);
  background: var(--pn-surface);
}

.fr-header,
.fr-actions,
.score-row,
.save-bar,
.field-header,
.field-actions,
.read-field-head,
.decision-strip {
  display: flex;
  align-items: center;
}

.fr-header {
  justify-content: space-between;
  gap: var(--pn-space-4);
  margin-bottom: var(--pn-space-5);
}

.fr-kicker,
.private-chip,
.decision-card span,
.field-header label,
.lp-title,
.lp-field-name,
.dirty-note,
.save-status {
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--pn-text-muted);
}

.fr-header h2 {
  margin-top: 2px;
  font-size: 22px;
}

.fr-actions {
  gap: var(--pn-space-2);
  flex-wrap: wrap;
  justify-content: flex-end;
}

.fr-model {
  border: 1px solid var(--pn-border);
  padding: 5px 8px;
  color: var(--pn-text-secondary);
  font-size: 10px;
  font-weight: 700;
}

.fr-ask {
  border-color: var(--pn-text);
  background: var(--pn-text);
  color: var(--pn-bg);
  font-weight: 700;
}

.fr-edit,
.field-btn,
.field-ask {
  font-size: 11px;
  font-weight: 700;
}

.decision-strip {
  gap: var(--pn-space-3);
  flex-wrap: wrap;
  padding-bottom: var(--pn-space-5);
  border-bottom: 1px solid var(--pn-border);
}

.decision-card {
  min-width: 132px;
  border: 1px solid var(--pn-border);
  background: var(--pn-bg);
  padding: var(--pn-space-3);
}

.decision-card strong {
  display: block;
  margin-top: 2px;
  font-size: 24px;
  line-height: 1;
}

.decision-card small {
  display: block;
  min-height: 18px;
  margin-top: 5px;
  color: var(--pn-text-secondary);
  overflow-wrap: anywhere;
}

.read-fields {
  display: grid;
  gap: var(--pn-space-4);
  margin-top: var(--pn-space-5);
}

.read-field {
  border-bottom: 1px solid var(--pn-border);
  padding-bottom: var(--pn-space-4);
}

.read-field:last-child {
  border-bottom: none;
  padding-bottom: 0;
}

.read-field-head {
  justify-content: space-between;
  gap: var(--pn-space-3);
  margin-bottom: var(--pn-space-2);
}

.read-field h3 {
  font-size: 16px;
}

.private-chip {
  color: var(--pn-warn);
}

.read-prose,
.read-list {
  color: var(--pn-text-secondary);
  font-family: var(--pn-serif);
  font-size: 15px;
  line-height: 1.72;
}

.read-prose :deep(p) {
  margin-bottom: var(--pn-space-3);
}

.read-list {
  padding-left: var(--pn-space-5);
}

.read-list li {
  margin-bottom: var(--pn-space-2);
}

.edit-mode {
  border-top: 1px solid var(--pn-border);
  padding-top: var(--pn-space-4);
}

.score-row {
  gap: var(--pn-space-4);
  align-items: flex-end;
  margin-bottom: var(--pn-space-4);
  padding-bottom: var(--pn-space-4);
  border-bottom: 1px solid var(--pn-border);
  flex-wrap: wrap;
}

.score-field label {
  display: block;
  margin-bottom: var(--pn-space-2);
  color: var(--pn-text-secondary);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.score-field select {
  min-width: 210px;
}

.lightpass-indicator {
  display: flex;
  align-items: center;
  gap: var(--pn-space-2);
  color: var(--pn-text-muted);
  font-size: 12px;
  margin-left: auto;
}

.lp-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--pn-warn);
  animation: pn-pulse 1.5s infinite;
}

.lightpass-banner {
  background: var(--pn-warn-dim);
  border: 1px solid var(--pn-warn);
  padding: var(--pn-space-4);
  margin-bottom: var(--pn-space-4);
}

.lp-suggestion {
  padding-top: var(--pn-space-3);
  margin-top: var(--pn-space-3);
  border-top: 1px solid var(--pn-border);
}

.lp-preview {
  color: var(--pn-text-secondary);
  line-height: 1.5;
  margin: var(--pn-space-2) 0;
}

.lp-actions {
  display: flex;
  gap: var(--pn-space-2);
}

.lp-btn.accept,
.save-btn,
.refine-send {
  background: var(--pn-text);
  border-color: var(--pn-text);
  color: var(--pn-bg);
  font-weight: 700;
}

.review-field-block {
  margin-bottom: var(--pn-space-4);
}

.field-header {
  gap: var(--pn-space-2);
  margin-bottom: var(--pn-space-2);
}

.field-hint {
  font-size: 10px;
  color: var(--pn-text-muted);
  font-style: italic;
}

.field-actions {
  margin-left: auto;
  gap: var(--pn-space-2);
}

.field-btn.active {
  background: var(--pn-text);
  border-color: var(--pn-text);
  color: var(--pn-bg);
}

.field-textarea {
  width: 100%;
  min-height: 120px;
  resize: vertical;
  line-height: 1.6;
}

.field-textarea.streaming {
  border-color: var(--pn-warn);
  background: var(--pn-warn-dim);
}

.refine-input-bar {
  display: flex;
  gap: var(--pn-space-2);
  margin-top: var(--pn-space-2);
}

.refine-input {
  flex: 1;
}

.save-bar {
  gap: var(--pn-space-3);
  margin-top: var(--pn-space-4);
  padding-top: var(--pn-space-3);
  border-top: 1px solid var(--pn-border);
}

.read-save {
  justify-content: flex-end;
}

@media (max-width: 760px) {
  .fr-header,
  .read-field-head,
  .score-row,
  .refine-input-bar {
    align-items: stretch;
    flex-direction: column;
  }

  .fr-actions,
  .field-actions {
    justify-content: flex-start;
  }

  .score-field select {
    width: 100%;
  }
}
</style>
