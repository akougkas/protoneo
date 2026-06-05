<template>
  <Teleport to="body">
    <div v-if="open" class="chair-layer">
      <div class="chair-scrim" @click="emit('close')"></div>
      <aside class="chair-drawer" role="dialog" aria-modal="true" aria-label="Ask PC Chair">
        <header class="chair-header">
          <div>
            <span class="chair-kicker">Grounded Advisor</span>
            <h2>Ask PC Chair</h2>
          </div>
          <button class="close-btn" @click="emit('close')" aria-label="Close PC Chair chat">Close</button>
        </header>

        <div v-if="focusedArtifact" class="focus-strip">
          <div>
            <span>Focused context</span>
            <strong>{{ focusedArtifact.label || 'Review context' }}</strong>
          </div>
          <button @click="emit('clear-focus')">Clear</button>
        </div>

        <div ref="chatLog" class="chat-log">
          <div v-if="loadingHistory" class="empty-state">Loading chair notes...</div>
          <div v-else-if="!turns.length" class="empty-state">
            <strong>PC Chair is ready.</strong>
            <span>Ask a question or request a small review edit.</span>
          </div>

          <article v-for="(turn, i) in turns" :key="turn._localId || i" class="turn">
            <div v-if="turn.user" class="bubble user-bubble">
              <div class="bubble-meta">You</div>
              <p>{{ turn.user }}</p>
            </div>

            <div class="bubble chair-bubble" :class="{ pending: turn.pending, error: turn.error }">
              <div class="bubble-meta">
                <span>PC Chair</span>
                <span v-if="turn.model">{{ shortModel(turn.model) }}</span>
              </div>
              <div v-if="turn.error" class="reply-text">{{ turn.error }}</div>
              <div v-else-if="turn.pending" class="reply-text">Reading the review packet...</div>
              <div v-else class="reply-text" v-html="md(turn.reply || '')"></div>

              <ul v-if="turn.edit_summary?.length" class="edit-summary">
                <li v-for="(item, idx) in turn.edit_summary" :key="idx">{{ item }}</li>
              </ul>

              <div v-if="proofItems(turn).length" class="proof-card">
                <div class="proof-head">Proof &amp; citations</div>
                <ul class="proof-list">
                  <li v-for="(proof, idx) in proofItems(turn)" :key="idx" :class="proof.kind">
                    <span class="proof-tag">{{ proof.tag }}</span>
                    <span class="proof-text">{{ proof.text }}</span>
                  </li>
                </ul>
              </div>

              <div v-if="hasPatch(turn.final_review_patch)" class="patch-card">
                <div class="patch-head">
                  <span>Suggested edits</span>
                  <strong>{{ patchRows(turn.final_review_patch).length }} fields</strong>
                </div>
                <div class="patch-row" v-for="row in patchRows(turn.final_review_patch)" :key="row.key">
                  <h4>{{ row.label }}</h4>
                  <div class="diff-grid">
                    <div>
                      <span>Current</span>
                      <p>{{ row.before }}</p>
                    </div>
                    <div>
                      <span>Suggestion</span>
                      <p>{{ row.after }}</p>
                    </div>
                  </div>
                </div>
                <div class="patch-actions">
                  <span v-if="turn.localState === 'applied'" class="patch-state applied">Applied to draft</span>
                  <span v-else-if="turn.localState === 'rejected'" class="patch-state rejected">Rejected</span>
                  <template v-else>
                    <button @click="applyPatch(turn)" class="apply-btn">Apply to Draft</button>
                    <button @click="rejectPatch(turn)">Reject</button>
                  </template>
                </div>
              </div>

              <div v-if="turn.needs_user_decision" class="decision-note">
                Needs chair decision before changing protected fields.
              </div>
            </div>
          </article>
        </div>

        <div class="quick-row">
          <button @click="sendQuick('Does the final review fairly reflect the strongest reviewer disagreement?')">
            Check disagreement
          </button>
          <button @click="sendQuick('Suggest a tighter author-facing wording pass without changing the score or recommendation.')">
            Wording pass
          </button>
          <button @click="sendQuick('What should I verify before saving this final review?')">
            Final check
          </button>
        </div>

        <form class="composer" @submit.prevent="sendMessage">
          <textarea
            v-model="message"
            :disabled="sending"
            placeholder="Ask the chair..."
            @keydown.meta.enter.prevent="sendMessage"
            @keydown.ctrl.enter.prevent="sendMessage"
          ></textarea>
          <div class="composer-foot">
            <select v-model="userRole" aria-label="User stance">
              <option value="chair_editor">Chair/editor</option>
              <option value="human_reviewer">Reviewer</option>
              <option value="conference_organizer">Organizer</option>
              <option value="author">Author</option>
            </select>
            <div class="composer-actions">
              <button v-if="draftApplied" type="button" @click="saveReview" :disabled="savingReview">
                {{ savingReview ? 'Saving...' : 'Save + Regenerate Exports' }}
              </button>
              <button type="submit" class="send-btn" :disabled="sending || !message.trim()">
                {{ sending ? 'Thinking...' : 'Send' }}
              </button>
            </div>
          </div>
          <div v-if="exportNote" class="export-note">
            <span>{{ exportNote }}</span>
            <button v-if="canDownload" type="button" @click="downloadReview" :disabled="downloading">
              {{ downloading ? 'Preparing...' : 'Download review' }}
            </button>
          </div>
        </form>
      </aside>
    </div>
  </Teleport>
</template>

<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import { pcChairChat, getPcChairChat, writeReviewArtifacts, getLinklingsReview } from '../api/kernel.js'
import { renderMarkdown } from '../utils/markdown.js'

const md = renderMarkdown

const props = defineProps({
  open: { type: Boolean, default: false },
  sessionId: { type: String, required: true },
  currentReview: { type: Object, default: () => ({}) },
  reviewPacket: { type: Object, default: null },
  focusedArtifact: { type: Object, default: null },
})

const emit = defineEmits(['close', 'apply-patch', 'save-review', 'clear-focus'])

const turns = ref([])
const message = ref('')
const sending = ref(false)
const loadingHistory = ref(false)
const userRole = ref('chair_editor')
const chatLog = ref(null)
const draftApplied = ref(false)
const savingReview = ref(false)
const exportNote = ref('')
const canDownload = ref(false)
const downloading = ref(false)

const selectedReviewField = computed(() => {
  const artifact = props.focusedArtifact
  if (!artifact) return ''
  if (artifact.metadata?.field) return artifact.metadata.field
  if (String(artifact.id || '').startsWith('final:')) return String(artifact.id).slice(6)
  return artifact.metadata?.section || ''
})

const selectedReviewExcerpt = computed(() => props.focusedArtifact?.excerpt || '')
const sessionMetadata = computed(() => props.reviewPacket?.provenance_metadata?.session_metadata || {})
const downloadPaperId = computed(() => {
  const candidate = sessionMetadata.value.packet_paper_id ||
    props.reviewPacket?.paper_id ||
    props.currentReview?.packet_paper_id ||
    props.currentReview?.paper_id ||
    props.sessionId
  return String(candidate || 'review').replace(/[^A-Za-z0-9._-]+/g, '_')
})

watch(() => props.open, async (value) => {
  if (value) {
    await fetchHistory()
    await nextTick()
    scrollToBottom()
  }
})

watch(() => turns.value.length, async () => {
  await nextTick()
  scrollToBottom()
})

async function fetchHistory() {
  loadingHistory.value = true
  try {
    const res = await getPcChairChat(props.sessionId)
    turns.value = (res.data?.history || []).map((turn, idx) => ({ ...turn, _localId: `history-${idx}` }))
  } catch (e) {
    turns.value = [{
      _localId: 'history-error',
      reply: '',
      error: 'Could not load PC Chair history.',
    }]
  } finally {
    loadingHistory.value = false
  }
}

async function sendQuick(text) {
  message.value = text
  await sendMessage()
}

async function sendMessage() {
  const text = message.value.trim()
  if (!text || sending.value) return

  const optimistic = {
    _localId: `local-${Date.now()}`,
    user: text,
    reply: '',
    pending: true,
    focused_artifact: props.focusedArtifact,
  }
  turns.value.push(optimistic)
  message.value = ''
  sending.value = true

  try {
    const res = await pcChairChat(props.sessionId, {
      message: text,
      currentReview: props.currentReview,
      applyEdits: false,
      userRole: userRole.value,
      focusedArtifact: props.focusedArtifact,
      selectedReviewField: selectedReviewField.value,
      selectedReviewExcerpt: selectedReviewExcerpt.value,
    })
    if (Array.isArray(res.data?.history)) {
      turns.value = res.data.history.map((turn, idx) => ({ ...turn, _localId: `history-${idx}-${turn.user?.slice(0, 8) || idx}` }))
    } else {
      Object.assign(optimistic, res.data || {}, { pending: false })
    }
  } catch (e) {
    optimistic.pending = false
    optimistic.error = e.response?.data?.detail || e.message || 'PC Chair chat failed.'
  } finally {
    sending.value = false
  }
}

function hasPatch(patch) {
  return Boolean(patch && typeof patch === 'object' && Object.keys(patch).length)
}

function patchRows(patch) {
  return Object.entries(patch || {}).map(([key, after]) => ({
    key,
    label: key.replace(/_/g, ' '),
    before: formatPatchValue(props.currentReview?.[key]),
    after: formatPatchValue(after),
  }))
}

function formatPatchValue(value) {
  if (value === null || value === undefined || value === '') return 'Empty'
  if (typeof value === 'string') return truncate(value, 900)
  if (typeof value !== 'object') return String(value)
  return truncate(JSON.stringify(value, null, 2), 1200)
}

function applyPatch(turn) {
  if (!hasPatch(turn.final_review_patch)) return
  emit('apply-patch', turn.final_review_patch)
  turn.localState = 'applied'
  draftApplied.value = true
}

function rejectPatch(turn) {
  turn.localState = 'rejected'
}

async function saveReview() {
  savingReview.value = true
  exportNote.value = ''
  try {
    const saved = await emitSave()
    if (saved !== false) {
      draftApplied.value = false
      // Persisted edits invalidate prior exports; regenerate from the saved review.
      try {
        const res = await writeReviewArtifacts(props.sessionId)
        const dir = res.data?.output_dir
        exportNote.value = dir ? `Exports regenerated: ${dir}` : 'Exports regenerated.'
        canDownload.value = true
      } catch (e) {
        exportNote.value = 'Saved, but export regeneration failed: ' + (e.response?.data?.detail || e.message || 'unknown')
        canDownload.value = false
      }
    }
  } finally {
    savingReview.value = false
  }
}

async function downloadReview() {
  downloading.value = true
  try {
    const res = await getLinklingsReview(props.sessionId)
    const blob = new Blob([res.data], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${downloadPaperId.value}_protoneo_offline_review.txt`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  } catch (e) {
    exportNote.value = 'Download failed: ' + (e.response?.data?.detail || e.message || 'unknown')
  } finally {
    downloading.value = false
  }
}

function proofItems(turn) {
  const items = []
  for (const cite of turn.citations || []) {
    if (!cite || typeof cite !== 'object') continue
    if (cite.source === 'query_graph') {
      items.push({
        kind: 'graph',
        tag: `graph:${cite.query_type || 'query'}`,
        text: cite.summary || `${cite.count ?? ''} result(s)`,
      })
    } else {
      const locator = [cite.section && `Section ${cite.section}`, cite.page && `p.${cite.page}`, cite.graph_ref]
        .filter(Boolean)
        .join(' · ')
      const claim = cite.claim || cite.point || ''
      const text = [claim, locator].filter(Boolean).join(' — ') || JSON.stringify(cite)
      items.push({ kind: 'manuscript', tag: 'cite', text })
    }
  }
  for (const tool of turn.tool_results || []) {
    if (!tool || typeof tool !== 'object' || tool.error) continue
    const summary = tool.result?.summary || ''
    items.push({ kind: 'graph', tag: `graph:${tool.query_type || 'query'}`, text: summary })
  }
  return items
}

function emitSave() {
  return new Promise((resolve) => {
    emit('save-review', resolve)
  })
}

function shortModel(model) {
  if (!model) return ''
  const parts = String(model).split('/')
  return parts[parts.length - 1].slice(0, 24)
}

function truncate(text, max) {
  const value = String(text || '')
  if (value.length <= max) return value
  return `${value.slice(0, max)}...`
}

function scrollToBottom() {
  if (chatLog.value) chatLog.value.scrollTop = chatLog.value.scrollHeight
}
</script>

<style scoped>
.chair-layer {
  position: fixed;
  inset: 0;
  z-index: 1200;
  pointer-events: auto;
}

.chair-scrim {
  position: absolute;
  inset: 0;
  background: rgba(10, 10, 10, 0.18);
}

.chair-drawer {
  position: absolute;
  top: 16px;
  right: 16px;
  bottom: 16px;
  width: min(560px, calc(100vw - 32px));
  display: grid;
  grid-template-rows: auto auto minmax(0, 1fr) auto auto;
  border: 1px solid var(--pn-border-strong);
  background: var(--pn-surface);
  box-shadow: 0 20px 70px rgba(10, 10, 10, 0.18);
}

.chair-header,
.focus-strip,
.composer-foot,
.patch-head,
.patch-actions,
.bubble-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--pn-space-3);
}

.chair-header {
  padding: var(--pn-space-5);
  border-bottom: 1px solid var(--pn-border);
}

.chair-kicker,
.focus-strip span,
.bubble-meta,
.patch-head span,
.diff-grid span,
.patch-state,
.decision-note {
  color: var(--pn-text-muted);
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.chair-header h2 {
  margin-top: 2px;
  font-size: 22px;
}

.close-btn {
  font-size: 11px;
  font-weight: 700;
}

.focus-strip {
  padding: var(--pn-space-3) var(--pn-space-5);
  border-bottom: 1px solid var(--pn-border);
  background: var(--pn-accent-dim);
}

.focus-strip strong {
  display: block;
  margin-top: 2px;
  font-size: 12px;
}

.chat-log {
  min-height: 0;
  overflow-y: auto;
  padding: var(--pn-space-5);
  display: flex;
  flex-direction: column;
  gap: var(--pn-space-4);
  background: var(--pn-bg);
}

.empty-state {
  border: 1px solid var(--pn-border);
  background: var(--pn-surface);
  padding: var(--pn-space-4);
  color: var(--pn-text-secondary);
  display: grid;
  gap: 4px;
}

.empty-state strong {
  color: var(--pn-text);
}

.turn {
  display: grid;
  gap: var(--pn-space-3);
}

.bubble {
  max-width: 88%;
  border: 1px solid var(--pn-border);
  padding: var(--pn-space-4);
  background: var(--pn-surface);
}

.user-bubble {
  justify-self: end;
  background: var(--pn-text);
  color: var(--pn-bg);
  border-color: var(--pn-text);
}

.user-bubble .bubble-meta {
  color: rgba(250, 250, 248, 0.68);
}

.chair-bubble {
  justify-self: start;
}

.chair-bubble.pending {
  border-color: var(--pn-warn);
  background: var(--pn-warn-dim);
}

.chair-bubble.error {
  border-color: var(--pn-err);
  background: var(--pn-err-dim);
}

.bubble p,
.reply-text {
  white-space: pre-wrap;
  line-height: 1.62;
}

.reply-text {
  color: var(--pn-text-secondary);
}

.reply-text :deep(p) {
  margin-bottom: var(--pn-space-2);
}

.edit-summary {
  margin-top: var(--pn-space-3);
  padding-left: var(--pn-space-4);
  color: var(--pn-text-secondary);
  font-size: 12px;
}

.proof-card {
  margin-top: var(--pn-space-3);
  border: 1px solid var(--pn-border);
  background: var(--pn-bg);
}

.proof-head {
  padding: var(--pn-space-2) var(--pn-space-3);
  border-bottom: 1px solid var(--pn-border);
  color: var(--pn-text-muted);
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.proof-list {
  list-style: none;
  margin: 0;
  padding: var(--pn-space-2) var(--pn-space-3);
  display: grid;
  gap: var(--pn-space-2);
}

.proof-list li {
  display: flex;
  gap: var(--pn-space-2);
  align-items: baseline;
  font-size: 12px;
  color: var(--pn-text-secondary);
}

.proof-tag {
  flex: none;
  font-size: 9px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  padding: 1px 6px;
  border: 1px solid var(--pn-border-strong);
  color: var(--pn-text-muted);
}

.proof-list li.graph .proof-tag {
  border-color: var(--pn-accent, var(--pn-border-strong));
}

.proof-text {
  overflow-wrap: anywhere;
}

.export-note {
  margin-top: var(--pn-space-3);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--pn-space-2);
  font-size: 11px;
  color: var(--pn-text-secondary);
  overflow-wrap: anywhere;
}

.patch-card {
  margin-top: var(--pn-space-3);
  border: 1px solid var(--pn-border-strong);
  background: var(--pn-bg);
}

.patch-head,
.patch-actions {
  padding: var(--pn-space-3);
  border-bottom: 1px solid var(--pn-border);
}

.patch-row {
  padding: var(--pn-space-3);
  border-bottom: 1px solid var(--pn-border);
}

.patch-row h4 {
  margin-bottom: var(--pn-space-2);
  font-size: 13px;
  text-transform: capitalize;
}

.diff-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--pn-space-3);
}

.diff-grid p {
  margin-top: 4px;
  color: var(--pn-text-secondary);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 12px;
}

.patch-actions {
  justify-content: flex-end;
  border-bottom: none;
}

.apply-btn,
.send-btn {
  background: var(--pn-text);
  border-color: var(--pn-text);
  color: var(--pn-bg);
  font-weight: 700;
}

.patch-state.applied {
  color: var(--pn-ok);
}

.patch-state.rejected,
.decision-note {
  color: var(--pn-err);
}

.decision-note {
  margin-top: var(--pn-space-3);
  border-top: 1px solid var(--pn-border);
  padding-top: var(--pn-space-2);
}

.quick-row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--pn-space-2);
  padding: var(--pn-space-3) var(--pn-space-5);
  border-top: 1px solid var(--pn-border);
}

.quick-row button {
  font-size: 10px;
  font-weight: 700;
}

.composer {
  border-top: 1px solid var(--pn-border);
  padding: var(--pn-space-4) var(--pn-space-5);
  background: var(--pn-surface);
}

.composer textarea {
  width: 100%;
  min-height: 116px;
  resize: vertical;
  line-height: 1.55;
}

.composer-foot {
  margin-top: var(--pn-space-3);
}

.composer-actions {
  display: flex;
  align-items: center;
  gap: var(--pn-space-2);
}

@media (max-width: 680px) {
  .chair-drawer {
    inset: 0;
    width: 100vw;
  }

  .bubble {
    max-width: 100%;
  }

  .diff-grid,
  .composer-foot {
    grid-template-columns: 1fr;
    display: grid;
  }

  .composer-actions {
    justify-content: flex-end;
  }
}
</style>
