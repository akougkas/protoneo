<template>
  <div class="final-review">
    <div class="fr-header">
      <h2>Final Review</h2>
      <span v-if="chairModel" class="fr-model">{{ shortModel(chairModel) }}</span>
    </div>

    <!-- Scores -->
    <div class="score-row">
      <div class="score-field">
        <label>Overall Merit</label>
        <select v-model.number="fields.overall_merit.score" @change="onScoreChange">
          <option :value="5">5 &ndash; Strong accept</option>
          <option :value="4">4 &ndash; Accept</option>
          <option :value="3">3 &ndash; Borderline</option>
          <option :value="2">2 &ndash; Weak reject</option>
          <option :value="1">1 &ndash; Reject</option>
        </select>
      </div>
      <div class="score-field">
        <label>Reviewer Expertise</label>
        <select v-model.number="fields.reviewer_expertise.score">
          <option :value="4">4 &ndash; Expert</option>
          <option :value="3">3 &ndash; Knowledgeable</option>
          <option :value="2">2 &ndash; Some familiarity</option>
          <option :value="1">1 &ndash; No familiarity</option>
        </select>
      </div>
      <div v-if="lightpassLoading" class="lightpass-indicator">
        <span class="lp-dot"></span> Checking alignment...
      </div>
    </div>

    <!-- Lightpass suggestions banner -->
    <div v-if="Object.keys(lightpassSuggestions).length > 0" class="lightpass-banner">
      <div class="lp-title">Score changed. Suggested edits:</div>
      <div v-for="(text, key) in lightpassSuggestions" :key="key" class="lp-suggestion">
        <div class="lp-field-name">{{ fieldLabels[key] || key }}</div>
        <div class="lp-preview">{{ text.substring(0, 200) }}{{ text.length > 200 ? '...' : '' }}</div>
        <div class="lp-actions">
          <button @click="acceptSuggestion(key, text)" class="lp-btn accept">Accept</button>
          <button @click="rejectSuggestion(key)" class="lp-btn reject">Dismiss</button>
        </div>
      </div>
    </div>

    <!-- Text fields -->
    <div v-for="f in textFieldDefs" :key="f.key" class="review-field-block">
      <div class="field-header">
        <label>{{ f.label }}</label>
        <span v-if="f.key === 'comments_for_pc'" class="field-hint">hidden from authors</span>
        <div class="field-actions">
          <button
            @click="toggleRefine(f.key)"
            :class="['field-btn', { active: refiningField === f.key }]"
            :disabled="refiningField && refiningField !== f.key"
          >Refine</button>
          <button @click="copyField(f.key)" class="field-btn">
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

      <!-- Inline refine input -->
      <div v-if="refiningField === f.key" class="refine-input-bar">
        <input
          v-model="refineInstruction"
          @keydown.enter.prevent="sendRefine(f.key)"
          :placeholder="`e.g. 'Be more specific about the I/O evaluation'`"
          :disabled="refineStreaming"
          class="refine-input"
          ref="refineInputEl"
        />
        <button @click="sendRefine(f.key)" :disabled="refineStreaming || !refineInstruction.trim()" class="refine-send">
          {{ refineStreaming ? 'Streaming...' : 'Send' }}
        </button>
        <button @click="cancelRefine" class="refine-cancel">Cancel</button>
      </div>
    </div>

    <!-- Save indicator -->
    <div class="save-bar">
      <button v-if="isDirty" @click="saveReview" class="save-btn" :disabled="saving">
        {{ saving ? 'Saving...' : 'Save Changes' }}
      </button>
      <span v-if="lastSaved" class="save-status">Saved {{ lastSaved }}</span>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, watch, nextTick } from 'vue'
import { refineField as apiRefineField, scoreLightpass, updateFinalReview } from '../api/kernel.js'

const props = defineProps({
  sessionId: { type: String, required: true },
  initialReview: { type: Object, default: () => ({}) },
  chairModel: { type: String, default: '' },
})

const emit = defineEmits(['review-updated'])

const fieldLabels = {
  paper_summary: 'Paper Summary',
  strengths: 'Strengths',
  weaknesses: 'Weaknesses',
  comments_for_authors: 'Comments for Authors',
  comments_for_pc: 'Comments for PC',
}

const textFieldDefs = [
  { key: 'paper_summary', label: 'Paper Summary', rows: 6 },
  { key: 'strengths', label: 'Strengths', rows: 8 },
  { key: 'weaknesses', label: 'Weaknesses', rows: 8 },
  { key: 'comments_for_authors', label: 'Comments for Authors', rows: 10 },
  { key: 'comments_for_pc', label: 'Comments for PC', rows: 5 },
]

const meritLabels = { 5: 'Strong accept', 4: 'Accept', 3: 'Borderline', 2: 'Weak reject', 1: 'Reject' }

const fields = reactive({
  overall_merit: { score: 3, label: 'Borderline' },
  reviewer_expertise: { score: 3, label: 'Knowledgeable' },
  paper_summary: '',
  strengths: '',
  weaknesses: '',
  comments_for_authors: '',
  comments_for_pc: '',
})

const refiningField = ref(null)
const refineInstruction = ref('')
const refineStreaming = ref(false)
const copiedField = ref(null)
const isDirty = ref(false)
const saving = ref(false)
const lastSaved = ref('')
const lightpassLoading = ref(false)
const lightpassSuggestions = ref({})
const refineInputEl = ref(null)

function shortModel(m) {
  if (!m) return ''
  const parts = m.split('/')
  return parts[parts.length - 1].slice(0, 28)
}

// Initialize from prop
function loadReview(data) {
  if (!data || typeof data !== 'object') return
  if (data.overall_merit) fields.overall_merit = { ...fields.overall_merit, ...data.overall_merit }
  if (data.reviewer_expertise) fields.reviewer_expertise = { ...fields.reviewer_expertise, ...data.reviewer_expertise }
  for (const key of Object.keys(fieldLabels)) {
    if (data[key] !== undefined) fields[key] = data[key]
  }
}
loadReview(props.initialReview)

watch(() => props.initialReview, (v) => loadReview(v), { deep: true })

function markDirty() { isDirty.value = true }

function getTextFields() {
  const out = {}
  for (const key of Object.keys(fieldLabels)) {
    out[key] = fields[key]
  }
  return out
}

function getAllFields() {
  return {
    overall_merit: fields.overall_merit,
    reviewer_expertise: fields.reviewer_expertise,
    ...getTextFields(),
  }
}

async function onScoreChange() {
  const score = fields.overall_merit.score
  fields.overall_merit.label = meritLabels[score] || ''
  isDirty.value = true

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
  fields[key] = text
  const remaining = { ...lightpassSuggestions.value }
  delete remaining[key]
  lightpassSuggestions.value = remaining
  isDirty.value = true
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
    nextTick(() => {
      const el = document.querySelector('.refine-input')
      if (el) el.focus()
    })
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
  fields[key] = '' // clear for streaming

  try {
    await apiRefineField(props.sessionId, key, refineInstruction.value, getTextFields())
    // Tokens arrive via WebSocket refine_token events handled by parent
  } catch (e) {
    fields[key] = `Error: ${e.message || 'Refine failed'}`
    refineStreaming.value = false
  }
}

// Called by parent when WebSocket events arrive
function handleRefineToken(field, chunk) {
  if (field === refiningField.value) {
    fields[field] += chunk
  }
}

function handleRefineDone(field, content) {
  if (field === refiningField.value) {
    fields[field] = content
    refineStreaming.value = false
    refineInstruction.value = ''
    isDirty.value = true
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
    await navigator.clipboard.writeText(fields[key] || '')
    copiedField.value = key
    setTimeout(() => { copiedField.value = null }, 1500)
  } catch (e) {
    console.error('Copy failed:', e)
  }
}

async function saveReview() {
  saving.value = true
  try {
    await updateFinalReview(props.sessionId, getAllFields())
    isDirty.value = false
    lastSaved.value = new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
    emit('review-updated', getAllFields())
  } catch (e) {
    console.error('Save failed:', e)
  } finally {
    saving.value = false
  }
}

defineExpose({ handleRefineToken, handleRefineDone, handleRefineError, loadReview })
</script>

<style scoped>
.final-review {
  border: 2px solid #000;
  border-radius: 8px;
  padding: 24px 28px;
  margin-bottom: 32px;
  background: #fff;
}

.fr-header {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 20px;
}

.fr-header h2 {
  font-size: 18px;
  font-weight: 700;
  letter-spacing: -0.3px;
  margin: 0;
}

.fr-model {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  padding: 2px 8px;
  border-radius: 3px;
  background: #f0f0f0;
  color: #888;
}

/* Scores */
.score-row {
  display: flex;
  gap: 20px;
  align-items: flex-end;
  margin-bottom: 20px;
  padding-bottom: 16px;
  border-bottom: 1px solid #eee;
}

.score-field label {
  display: block;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #555;
  margin-bottom: 6px;
}

.score-field select {
  padding: 8px 12px;
  font-size: 14px;
  font-weight: 600;
  border: 2px solid #ddd;
  border-radius: 4px;
  background: #fff;
  cursor: pointer;
  min-width: 200px;
}

.score-field select:focus {
  border-color: #000;
  outline: none;
}

.lightpass-indicator {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #888;
  margin-left: auto;
}

.lp-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #e8a500;
  animation: pulse 1.5s infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}

/* Lightpass banner */
.lightpass-banner {
  background: #fffdf0;
  border: 1px solid #f0e0a0;
  border-radius: 6px;
  padding: 14px 18px;
  margin-bottom: 16px;
}

.lp-title {
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #a07000;
  margin-bottom: 10px;
}

.lp-suggestion {
  padding: 10px 0;
  border-top: 1px solid #f0e0a0;
}

.lp-field-name {
  font-size: 12px;
  font-weight: 600;
  color: #555;
  margin-bottom: 4px;
}

.lp-preview {
  font-size: 12px;
  color: #666;
  line-height: 1.5;
  margin-bottom: 6px;
}

.lp-actions {
  display: flex;
  gap: 8px;
}

.lp-btn {
  padding: 4px 14px;
  font-size: 11px;
  font-weight: 600;
  border-radius: 3px;
  cursor: pointer;
  border: 1px solid;
}

.lp-btn.accept {
  background: #000;
  color: #fff;
  border-color: #000;
}

.lp-btn.reject {
  background: #fff;
  color: #666;
  border-color: #ddd;
}

.lp-btn.reject:hover { border-color: #999; }

/* Review field blocks */
.review-field-block {
  margin-bottom: 16px;
}

.field-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.field-header label {
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #333;
}

.field-hint {
  font-size: 10px;
  color: #999;
  font-style: italic;
}

.field-actions {
  margin-left: auto;
  display: flex;
  gap: 6px;
}

.field-btn {
  padding: 3px 12px;
  font-size: 11px;
  font-weight: 600;
  border: 1px solid #ddd;
  background: #fff;
  border-radius: 3px;
  cursor: pointer;
  color: #555;
}

.field-btn:hover { border-color: #000; color: #000; }
.field-btn.active { background: #000; color: #fff; border-color: #000; }
.field-btn:disabled { opacity: 0.3; cursor: default; }

.field-textarea {
  width: 100%;
  padding: 12px 14px;
  font-size: 13px;
  font-family: 'Inter', -apple-system, sans-serif;
  line-height: 1.6;
  border: 1px solid #ddd;
  border-radius: 4px;
  resize: vertical;
  outline: none;
  box-sizing: border-box;
}

.field-textarea:focus {
  border-color: #000;
}

.field-textarea.streaming {
  border-color: #e8a500;
  background: #fffdf5;
}

/* Refine input */
.refine-input-bar {
  display: flex;
  gap: 6px;
  margin-top: 6px;
}

.refine-input {
  flex: 1;
  padding: 8px 12px;
  font-size: 12px;
  border: 1px solid #ddd;
  border-radius: 4px;
  outline: none;
}

.refine-input:focus { border-color: #000; }

.refine-send {
  padding: 8px 16px;
  font-size: 12px;
  font-weight: 600;
  background: #000;
  color: #fff;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}

.refine-send:hover { background: #222; }
.refine-send:disabled { background: #ccc; cursor: default; }

.refine-cancel {
  padding: 8px 12px;
  font-size: 12px;
  background: none;
  border: 1px solid #ddd;
  border-radius: 4px;
  cursor: pointer;
  color: #888;
}

.refine-cancel:hover { border-color: #999; color: #333; }

/* Save bar */
.save-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid #eee;
}

.save-btn {
  padding: 8px 24px;
  font-size: 13px;
  font-weight: 600;
  background: #000;
  color: #fff;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}

.save-btn:hover { background: #222; }
.save-btn:disabled { background: #ccc; cursor: default; }

.save-status {
  font-size: 11px;
  color: #999;
}
</style>
