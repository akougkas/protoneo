<template>
  <div class="review-packet">
    <!-- Header -->
    <div class="packet-header">
      <h2>{{ packet.paper_title || 'Review Packet' }}</h2>
      <div class="packet-meta">
        <span v-if="packet.conference">{{ packet.conference.toUpperCase() }}</span>
        <span v-if="packet.duration_seconds">
          {{ Math.floor(packet.duration_seconds) }}s
        </span>
        <span v-if="packet.total_cost > 0" class="cost-badge">
          ${{ packet.total_cost.toFixed(4) }}
        </span>
      </div>
    </div>

    <!-- Score Overview -->
    <div v-if="scores.length > 0" class="score-overview">
      <h3>Score Overview</h3>
      <div class="score-grid">
        <div v-for="s in scores" :key="s.role" class="score-item">
          <div class="score-role">{{ s.role }}</div>
          <div :class="['score-badge', scoreTier(s.score)]">{{ s.score }}</div>
          <div class="score-label">{{ s.label }}</div>
        </div>
      </div>
    </div>

    <!-- Meta-Review Summary (top, for quick reading) -->
    <div v-if="meta.author_facing_summary || meta.panel_summary" class="meta-summary-box">
      <h3>Panel Summary</h3>
      <div class="summary-text review-body-md" v-html="md(meta.panel_summary || meta.author_facing_summary)"></div>
      <div v-if="meta.final_recommendation && meta.final_recommendation.score" class="final-rec">
        <span class="rec-label">Final Recommendation:</span>
        <span :class="['rec-score', scoreTier(meta.final_recommendation.score)]">
          {{ meta.final_recommendation.score }} / 5
        </span>
        <span class="rec-verdict">{{ meta.final_recommendation.label }}</span>
      </div>
      <div v-if="meta.consensus && meta.consensus.level" class="consensus-line">
        Consensus: <strong>{{ meta.consensus.level }}</strong>
        <span v-if="meta.consensus.summary"> &mdash; {{ meta.consensus.summary }}</span>
      </div>
    </div>

    <!-- Graph Context (what reviewers received) -->
    <div v-if="packet.graph_node_count > 0" class="graph-context-section">
      <h3 @click="showGraphCtx = !showGraphCtx" class="collapsible-header">
        Knowledge Graph Context
        <span class="graph-ctx-stats">
          {{ packet.graph_node_count }} entities, {{ packet.graph_edge_count }} relationships
        </span>
        <span class="toggle-icon">{{ showGraphCtx ? '−' : '+' }}</span>
      </h3>
      <pre v-if="showGraphCtx && packet.graph_summary" class="graph-summary-block">{{ packet.graph_summary }}</pre>
    </div>

    <!-- Graph Utilization -->
    <div v-if="utilization && utilization.utilization_ratio !== undefined" class="utilization-section">
      <h3 @click="showUtilization = !showUtilization" class="collapsible-header">
        Graph Utilization
        <span class="graph-ctx-stats">
          {{ Math.round(utilization.utilization_ratio * 100) }}% entities referenced
        </span>
        <span class="toggle-icon">{{ showUtilization ? '−' : '+' }}</span>
      </h3>
      <div v-if="showUtilization" class="utilization-body">
        <!-- Per-reviewer coverage -->
        <div v-if="Object.keys(utilization.per_reviewer || {}).length > 0" class="util-reviewer-bars">
          <h4>Reviewer Coverage</h4>
          <div v-for="(info, aid) in utilization.per_reviewer" :key="aid" class="util-bar-row">
            <span class="util-bar-label">{{ aid }}</span>
            <div class="util-bar-track">
              <div class="util-bar-fill" :style="{ width: Math.round(info.coverage_ratio * 100) + '%' }"></div>
            </div>
            <span class="util-bar-pct">{{ Math.round(info.coverage_ratio * 100) }}%</span>
          </div>
        </div>

        <!-- By type -->
        <div v-if="Object.keys(utilization.by_type || {}).length > 0" class="util-type-grid">
          <h4>Coverage by Entity Type</h4>
          <div class="util-type-chips">
            <span
              v-for="(info, etype) in utilization.by_type"
              :key="etype"
              :class="['util-type-chip', info.ratio >= 0.7 ? 'good' : info.ratio >= 0.3 ? 'partial' : 'low']"
            >
              {{ etype }}: {{ info.referenced }}/{{ info.total }}
            </span>
          </div>
        </div>

        <!-- Blind spots -->
        <div v-if="(utilization.unreferenced_entities || []).length > 0" class="util-blind-spots">
          <h4>Blind Spots ({{ utilization.unreferenced_entities.length }} unreferenced)</h4>
          <div class="blind-spot-list">
            <span
              v-for="e in utilization.unreferenced_entities.slice(0, 20)"
              :key="e.entity_id"
              class="blind-spot-chip"
            >{{ e.label }} <small>({{ e.type }})</small></span>
            <span v-if="utilization.unreferenced_entities.length > 20" class="blind-spot-more">
              +{{ utilization.unreferenced_entities.length - 20 }} more
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- Individual Reviews -->
    <div class="reviews-section">
      <h3>Individual Reviews</h3>
      <div v-for="(review, idx) in packet.reviews" :key="review.reviewer_role || review.agent_id || idx" class="review-card">
        <div class="review-header" @click="toggleReview(idx)">
          <div class="review-role">{{ review.reviewer_role }}</div>
          <div class="review-header-right">
            <span v-if="review.overall_merit && review.overall_merit.score"
              :class="['score-badge small', scoreTier(review.overall_merit.score)]">
              {{ review.overall_merit.score }}
            </span>
            <span class="expand-icon">{{ expandedReviews[idx] ? '−' : '+' }}</span>
          </div>
        </div>

        <div v-if="expandedReviews[idx]" class="review-body">
          <!-- Summary -->
          <div v-if="review.summary" class="review-field">
            <h4>Summary</h4>
            <div class="review-body-md" v-html="md(review.summary)"></div>
          </div>

          <!-- Score details -->
          <div v-if="review.overall_merit && review.overall_merit.score" class="review-field score-detail">
            <div>
              <span class="field-label">Merit:</span>
              {{ review.overall_merit.score }} ({{ review.overall_merit.label || meritLabel(review.overall_merit.score) }})
            </div>
            <div v-if="review.expertise && review.expertise.score">
              <span class="field-label">Expertise:</span>
              {{ review.expertise.score }} ({{ review.expertise.label || '' }})
            </div>
            <div v-if="review.confidence && review.confidence.score">
              <span class="field-label">Confidence:</span>
              {{ review.confidence.score }}
            </div>
          </div>

          <!-- Strengths -->
          <div v-if="review.strengths && review.strengths.length" class="review-field">
            <h4>Strengths</h4>
            <ul class="tagged-list strengths">
              <li v-for="(s, si) in review.strengths" :key="review.reviewer_role + '-str-' + si">
                <template v-if="typeof s === 'object'">
                  <strong>{{ itemPrimary(s) }}</strong>
                  <span v-if="itemEvidence(s)" class="evidence">{{ itemEvidence(s) }}</span>
                  <span v-for="tag in itemTags(s)" :key="tag" :class="['tag', tagClass(tag)]">{{ tag }}</span>
                </template>
                <template v-else>{{ formatReviewItem(s) }}</template>
              </li>
            </ul>
          </div>

          <!-- Weaknesses -->
          <div v-if="review.weaknesses && review.weaknesses.length" class="review-field">
            <h4>Weaknesses</h4>
            <ul class="tagged-list weaknesses">
              <li v-for="(w, wi) in review.weaknesses" :key="review.reviewer_role + '-weak-' + wi">
                <template v-if="typeof w === 'object'">
                  <strong>{{ itemPrimary(w) }}</strong>
                  <span v-if="itemEvidence(w)" class="evidence">{{ itemEvidence(w) }}</span>
                  <span v-for="tag in itemTags(w)" :key="tag" :class="['tag', tagClass(tag)]">{{ tag }}</span>
                </template>
                <template v-else>{{ formatReviewItem(w) }}</template>
              </li>
            </ul>
          </div>

          <!-- Questions -->
          <div v-if="review.questions_for_authors && review.questions_for_authors.length" class="review-field">
            <h4>Questions for Authors</h4>
            <ol>
              <li v-for="(q, qi) in review.questions_for_authors" :key="review.reviewer_role + '-q-' + qi">
                {{ formatReviewItem(q) }}
              </li>
            </ol>
          </div>

          <!-- Comments -->
          <div v-if="review.comments_for_authors" class="review-field">
            <h4>Comments for Authors</h4>
            <div class="review-body-md" v-html="md(review.comments_for_authors)"></div>
          </div>

          <!-- Committee Concerns -->
          <div v-if="review.internal_committee_concerns && review.internal_committee_concerns.length"
            class="review-field concerns">
            <h4>Decision Risk Notes</h4>
            <ul>
              <li v-for="(c, ci) in review.internal_committee_concerns" :key="review.reviewer_role + '-concern-' + ci">
                {{ formatReviewItem(c) }}
              </li>
            </ul>
          </div>

          <!-- Revision Actions -->
          <div v-if="review.revision_actions && review.revision_actions.length" class="review-field">
            <h4>Revision Actions</h4>
            <div v-for="(ra, ri) in review.revision_actions" :key="review.reviewer_role + '-rev-' + ri" class="revision-item">
              <span :class="['priority-tag', ra.priority || 'should']">
                {{ ra.priority || 'should' }}
              </span>
              <span class="revision-text">{{ revisionText(ra) }}</span>
              <span v-if="ra.target_section" class="revision-target">
                &rarr; {{ ra.target_section }}
              </span>
            </div>
          </div>

          <!-- Model info -->
          <div class="review-footer">
            <span v-if="review.model" class="model-tag">{{ review.model }}</span>
            <span class="agent-tag">{{ review.agent_id }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Deliberation Log -->
    <div v-if="packet.deliberation && packet.deliberation.length" class="delib-section">
      <h3 @click="showDelib = !showDelib" class="collapsible">
        Deliberation Log ({{ totalDelibEntries }} exchanges)
        <span class="toggle">{{ showDelib ? '−' : '+' }}</span>
      </h3>
      <div v-if="showDelib">
        <div v-for="round in packet.deliberation" :key="round.round_number" class="delib-round">
          <div class="round-label">Round {{ round.round_number }}</div>
          <div v-for="(entry, ei) in round.entries" :key="round.round_number + '-' + ei" class="delib-entry">
            <div class="delib-role">{{ entry.role }}</div>
            <div class="delib-content" v-html="md(truncate(entry.content, 500))"></div>
          </div>
        </div>
      </div>
    </div>

    <!-- Full Meta-Review -->
    <div v-if="hasMetaDetail" class="meta-section">
      <h3>Meta-Review Details</h3>

      <div v-if="meta.agreements && meta.agreements.length" class="meta-field">
        <h4>Points of Agreement</h4>
        <ul>
          <li v-for="(a, ai) in meta.agreements" :key="'agree-' + ai">{{ formatReviewItem(a) }}</li>
        </ul>
      </div>

      <div v-if="meta.disagreements && meta.disagreements.length" class="meta-field">
        <h4>Points of Disagreement</h4>
        <div v-for="(d, di) in meta.disagreements" :key="'disagree-' + di" class="disagreement-item">
          <strong>{{ itemPrimary(d) }}</strong>
          <p v-if="d.why_reviewers_disagree">{{ d.why_reviewers_disagree }}</p>
          <p v-if="d.your_resolution" class="resolution">
            Resolution: {{ d.your_resolution }}
          </p>
        </div>
      </div>

      <div v-if="meta.decision_risk_notes && meta.decision_risk_notes.length" class="meta-field concerns">
        <h4>Decision Risk Notes</h4>
        <ul>
          <li v-for="(n, ni) in meta.decision_risk_notes" :key="'risk-' + ni">{{ formatReviewItem(n) }}</li>
        </ul>
      </div>

      <div v-if="meta.prioritized_revision_plan && meta.prioritized_revision_plan.length"
        class="meta-field revision-plan">
        <h4>Prioritized Revision Plan</h4>
        <div v-for="(item, ii) in meta.prioritized_revision_plan" :key="'revplan-' + ii" class="revision-item">
          <span :class="['priority-tag', item.priority || 'should']">
            {{ item.priority || 'should' }}
          </span>
          <span class="revision-text">{{ revisionText(item) }}</span>
          <span v-if="item.target_section" class="revision-target">
            &rarr; {{ item.target_section }}
          </span>
          <span v-if="item.expected_review_impact" class="revision-impact">
            {{ item.expected_review_impact }}
          </span>
        </div>
      </div>

      <div v-if="meta.submission_readiness && meta.submission_readiness.status" class="readiness-box">
        <div class="readiness-label">Submission Readiness</div>
        <div :class="['readiness-status', meta.submission_readiness.status]">
          {{ formatReadiness(meta.submission_readiness.status) }}
        </div>
        <p v-if="meta.submission_readiness.reason">{{ meta.submission_readiness.reason }}</p>
      </div>
    </div>

    <!-- Export Controls -->
    <div class="export-section">
      <h3 class="export-heading">Export</h3>
      <div class="export-grid">
        <div v-for="fmt in exportCards" :key="fmt.format_name" class="export-card" @click="doExport(fmt.format_name)">
          <div class="export-icon">{{ fmt.icon }}</div>
          <div class="export-label">{{ fmt.label }}</div>
          <div class="export-format">{{ fmt.ext }}</div>
        </div>
        <div class="export-card" @click="exportGraphJSON">
          <div class="export-icon">KG</div>
          <div class="export-label">Knowledge Graph</div>
          <div class="export-format">JSON</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, inject, onMounted } from 'vue'
import { getReviewPacketMd, getReviewPacketPdf, exportGraph, getExportFormats, exportSession } from '../api/kernel.js'
import { renderMarkdown } from '../utils/markdown.js'

const md = renderMarkdown

const activeApp = inject('activeApp', ref(null))
const scoreFields = computed(() => activeApp.value?.score_fields || [])

const props = defineProps({
  packet: { type: Object, required: true },
})

const expandedReviews = ref({})
const showGraphCtx = ref(false)
const showUtilization = ref(false)
const showDelib = ref(false)
const availableFormats = ref([])

onMounted(async () => {
  try {
    const res = await getExportFormats()
    availableFormats.value = res.data.formats || []
  } catch (e) {
    console.warn('Failed to load export formats:', e)
  }
})

// Expand the first review by default
if (props.packet.reviews?.length > 0) {
  expandedReviews.value[0] = true
}

const meta = computed(() => props.packet.meta_review || {})
const utilization = computed(() => props.packet.graph_utilization || null)

const scores = computed(() => {
  return (props.packet.reviews || [])
    .filter(r => r.overall_merit && r.overall_merit.score)
    .map(r => ({
      role: r.reviewer_role,
      score: r.overall_merit.score,
      label: r.overall_merit.label || meritLabel(r.overall_merit.score),
    }))
})

const totalDelibEntries = computed(() => {
  return (props.packet.deliberation || [])
    .reduce((sum, r) => sum + (r.entries?.length || 0), 0)
})

const hasMetaDetail = computed(() => {
  const m = meta.value
  return (m.agreements?.length > 0) ||
    (m.disagreements?.length > 0) ||
    (m.decision_risk_notes?.length > 0) ||
    (m.prioritized_revision_plan?.length > 0) ||
    (m.submission_readiness?.status)
})

function meritLabel(score) {
  // Use score_fields from manifest if available
  const sf = scoreFields.value.find(f => f.name === 'overall_merit')
  if (sf && sf.labels && sf.labels[score]) return sf.labels[score]
  const defaults = { 1: 'Reject', 2: 'Weak reject', 3: 'Borderline', 4: 'Accept', 5: 'Strong accept' }
  return defaults[score] || ''
}

function scoreTier(score) {
  if (score >= 4) return 'high'
  if (score >= 3) return 'mid'
  return 'low'
}

function formatReviewItem(value) {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  if (typeof value !== 'object') return String(value)
  if (Array.isArray(value)) return value.map(formatReviewItem).filter(Boolean).join('; ')

  const primary = itemPrimary(value)
  const details = []
  if (value.evidence) details.push(`Evidence: ${formatReviewItem(value.evidence)}`)
  if (value.target_section) details.push(`Target: ${formatReviewItem(value.target_section)}`)
  if (value.why_it_matters) details.push(`Why it matters: ${formatReviewItem(value.why_it_matters)}`)
  if (value.expected_review_impact) details.push(`Expected impact: ${formatReviewItem(value.expected_review_impact)}`)
  return [primary, ...itemTags(value).map(t => `[${t}]`), ...details].filter(Boolean).join(' — ')
}

function itemPrimary(value) {
  if (value === null || value === undefined) return ''
  if (typeof value !== 'object') return String(value)
  if (Array.isArray(value)) return formatReviewItem(value)
  return value.point || value.action || value.question || value.concern ||
    value.issue || value.claim || value.text || value.description ||
    value.summary || value.recommendation ||
    Object.entries(value)
      .filter(([, v]) => v !== null && v !== undefined && v !== '')
      .map(([k, v]) => `${k.replace(/_/g, ' ')}: ${formatReviewItem(v)}`)
      .join('; ')
}

function itemEvidence(value) {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value.evidence || '')
    : ''
}

function itemTags(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return []
  return ['severity', 'importance', 'priority', 'fixability']
    .filter(k => value[k])
    .map(k => `${k.replace(/_/g, ' ')}: ${formatReviewItem(value[k])}`)
}

function tagClass(tag) {
  return String(tag).toLowerCase().replace(/[^a-z0-9_-]+/g, '-')
}

function revisionText(value) {
  if (value && typeof value === 'object' && !Array.isArray(value) && value.action) {
    return value.action
  }
  return formatReviewItem(value)
}

function toggleReview(idx) {
  expandedReviews.value[idx] = !expandedReviews.value[idx]
}

function truncate(text, max) {
  if (!text) return ''
  return text.length > max ? text.substring(0, max) + '...' : text
}

function formatReadiness(status) {
  return status.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}

const FORMAT_ICONS = {
  json: '{ }', markdown: 'MD', 'review-markdown': 'MD', pdf: 'PDF', 'review-pdf': 'PDF', latex: 'TEX',
}

const exportCards = computed(() => {
  if (availableFormats.value.length > 0) {
    return availableFormats.value.map(f => ({
      format_name: f.format_name,
      icon: FORMAT_ICONS[f.format_name] || f.file_extension.replace('.', '').toUpperCase(),
      label: f.format_name.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase()),
      ext: f.file_extension.replace('.', '').toUpperCase(),
    }))
  }
  // Fallback if endpoint not available
  return [
    { format_name: 'json', icon: '{ }', label: 'Full Packet', ext: 'JSON' },
    { format_name: 'review-markdown', icon: 'MD', label: 'Full Packet', ext: 'MD' },
    { format_name: 'review-pdf', icon: 'PDF', label: 'Full Packet', ext: 'PDF' },
  ]
})

async function doExport(formatName) {
  const sid = props.packet.session_id || 'export'
  if (formatName === 'json') {
    const blob = new Blob([JSON.stringify(props.packet, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `review-packet-${sid}.json`
    a.click()
    URL.revokeObjectURL(url)
    return
  }
  try {
    const res = await exportSession(sid, formatName)
    const url = URL.createObjectURL(res.data)
    const fmt = availableFormats.value.find(f => f.format_name === formatName)
    const ext = fmt ? fmt.file_extension : '.bin'
    const a = document.createElement('a')
    a.href = url
    a.download = `review-packet-${sid}${ext}`
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    console.error(`Failed to export ${formatName}:`, e)
  }
}

async function exportGraphJSON() {
  try {
    const res = await exportGraph(props.packet.session_id)
    const url = URL.createObjectURL(res.data)
    const a = document.createElement('a')
    a.href = url
    a.download = `graph-${props.packet.session_id || 'export'}.json`
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    console.error('Failed to export graph:', e)
  }
}

</script>

<style scoped>
.review-packet {
  margin-top: 32px;
  border-top: 2px solid #000;
  padding-top: 24px;
}

.packet-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 24px;
}

.packet-header h2 {
  font-size: 20px;
  font-weight: 700;
}

.packet-meta {
  display: flex;
  gap: 16px;
  font-size: 13px;
  color: #666;
  font-family: 'JetBrains Mono', monospace;
}

.cost-badge {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  color: #888;
}

/* Score overview */
.score-overview {
  margin-bottom: 24px;
}

.score-overview h3 {
  font-size: 13px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #666;
  margin-bottom: 12px;
}

.score-grid {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}

.score-item {
  text-align: center;
  min-width: 100px;
}

.score-role {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #666;
  margin-bottom: 6px;
}

.score-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  font-weight: 700;
  font-size: 18px;
  border: 2px solid;
}

.score-badge.small {
  width: 28px;
  height: 28px;
  font-size: 13px;
}

.score-badge.high { border-color: #4a4; color: #2a2; background: #f0f8f0; }
.score-badge.mid { border-color: #e8a500; color: #a07000; background: #fffdf0; }
.score-badge.low { border-color: #c44; color: #900; background: #fff5f5; }

.score-label {
  font-size: 11px;
  color: #888;
  margin-top: 4px;
}

/* Meta summary box */
.meta-summary-box {
  border: 1px solid #e0e0e0;
  border-radius: 6px;
  padding: 20px 24px;
  margin-bottom: 24px;
  background: #fafafa;
}

.meta-summary-box h3 {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 10px;
}

.summary-text {
  font-size: 14px;
  line-height: 1.7;
  color: #333;
}

.final-rec {
  margin-top: 14px;
  display: flex;
  align-items: center;
  gap: 10px;
}

.rec-label {
  font-size: 13px;
  font-weight: 600;
}

.rec-score {
  font-weight: 700;
  font-size: 15px;
  padding: 2px 10px;
  border-radius: 4px;
}

.rec-score.high { background: #e8f5e9; color: #2a2; }
.rec-score.mid { background: #fff8e1; color: #a07000; }
.rec-score.low { background: #ffebee; color: #900; }

.rec-verdict {
  font-size: 14px;
  font-weight: 500;
}

.consensus-line {
  margin-top: 8px;
  font-size: 13px;
  color: #555;
}

/* Individual reviews */
.reviews-section {
  margin-bottom: 24px;
}

.reviews-section h3 {
  font-size: 14px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 12px;
}

.review-card {
  border: 1px solid #e0e0e0;
  border-radius: 5px;
  margin-bottom: 8px;
  overflow: hidden;
}

.review-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 16px;
  cursor: pointer;
  transition: background 0.1s;
}

.review-header:hover {
  background: #fafafa;
}

.review-role {
  font-weight: 600;
  font-size: 14px;
}

.review-header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.expand-icon {
  font-size: 18px;
  color: #999;
  width: 20px;
  text-align: center;
}

.review-body {
  padding: 0 16px 16px;
  border-top: 1px solid #f0f0f0;
}

.review-field {
  margin-top: 16px;
}

.review-field h4 {
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #666;
  margin-bottom: 8px;
}

.review-field p {
  font-size: 14px;
  line-height: 1.6;
  color: #333;
}

.score-detail {
  display: flex;
  gap: 24px;
  font-size: 13px;
  color: #555;
}

.field-label {
  font-weight: 600;
}

/* Tagged lists */
.tagged-list {
  list-style: none;
  padding: 0;
}

.tagged-list li {
  padding: 8px 0;
  border-bottom: 1px solid #f4f4f4;
  font-size: 14px;
  line-height: 1.5;
}

.tagged-list li:last-child {
  border-bottom: none;
}

.evidence {
  display: block;
  font-size: 12px;
  color: #888;
  margin-top: 2px;
  font-style: italic;
}

.tag {
  display: inline-block;
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  padding: 1px 6px;
  border-radius: 3px;
  margin-left: 8px;
  background: #f0f0f0;
  color: #666;
}

.tag.high { background: #ffebee; color: #900; }
.tag.medium { background: #fff8e1; color: #a07000; }
.tag.low { background: #e8f5e9; color: #2a2; }

.comments-text {
  white-space: pre-wrap;
}

/* Rendered markdown */
.review-body-md h2, .review-body-md h3, .review-body-md h4,
.card-chair-review-content h2, .card-chair-review-content h3, .card-chair-review-content h4 {
  font-size: 14px; font-weight: 600; margin: 16px 0 8px; color: #222;
}
.review-body-md strong, .card-chair-review-content strong { font-weight: 600; }
.review-body-md code, .card-chair-review-content code {
  font-family: 'JetBrains Mono', monospace; font-size: 12px;
  background: #f4f4f4; padding: 1px 4px; border-radius: 2px;
}
.review-body-md li, .card-chair-review-content li { margin-bottom: 4px; }
.review-body-md p, .card-chair-review-content p { margin: 0 0 8px; }
.md-table { border-collapse: collapse; font-size: 12px; margin: 8px 0; width: 100%; }
.md-table td { border: 1px solid #ddd; padding: 4px 8px; }
.md-table tr:first-child td { font-weight: 600; background: #f8f8f8; }

.concerns {
  background: #fffdf5;
  border: 1px solid #f0e8d0;
  border-radius: 4px;
  padding: 12px 16px;
}

/* Revision items */
.revision-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 8px 0;
  border-bottom: 1px solid #f4f4f4;
  font-size: 13px;
}

.priority-tag {
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  padding: 2px 8px;
  border-radius: 3px;
  flex-shrink: 0;
  margin-top: 2px;
}

.priority-tag.must { background: #ffebee; color: #900; }
.priority-tag.should { background: #fff8e1; color: #a07000; }
.priority-tag.nice, .priority-tag.could { background: #e8f5e9; color: #2a2; }

.revision-text { flex: 1; }
.revision-target { color: #888; font-size: 12px; }
.revision-impact { color: #666; font-size: 12px; font-style: italic; }

.review-footer {
  margin-top: 16px;
  padding-top: 8px;
  border-top: 1px solid #f0f0f0;
  display: flex;
  gap: 8px;
}

.model-tag, .agent-tag {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  padding: 2px 6px;
  border-radius: 3px;
  background: #f0f0f0;
  color: #888;
}

/* Deliberation */
.delib-section {
  margin-bottom: 24px;
  border: 1px solid #e8e8e8;
  border-radius: 5px;
  padding: 16px;
}

.delib-section h3 {
  font-size: 13px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #666;
}

.collapsible {
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.toggle {
  font-size: 16px;
  font-weight: 400;
}

.delib-round {
  margin-top: 16px;
}

.round-label {
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #999;
  margin-bottom: 8px;
}

.delib-entry {
  padding: 10px 0;
  border-bottom: 1px solid #f4f4f4;
}

.delib-role {
  font-weight: 600;
  font-size: 12px;
  color: #555;
  margin-bottom: 4px;
}

.delib-content {
  font-size: 13px;
  line-height: 1.5;
  color: #444;
  white-space: pre-wrap;
}

/* Meta details */
.meta-section {
  margin-bottom: 24px;
}

.meta-section h3 {
  font-size: 14px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 16px;
}

.meta-field {
  margin-bottom: 16px;
}

.meta-field h4 {
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #666;
  margin-bottom: 8px;
}

.meta-field ul {
  padding-left: 18px;
}

.meta-field li {
  font-size: 14px;
  line-height: 1.6;
  margin-bottom: 4px;
}

.disagreement-item {
  padding: 10px 0;
  border-bottom: 1px solid #f0f0f0;
  font-size: 14px;
}

.resolution {
  color: #555;
  font-style: italic;
  margin-top: 4px;
}

.readiness-box {
  border: 2px solid #e0e0e0;
  border-radius: 6px;
  padding: 16px 20px;
  margin-top: 16px;
}

.readiness-label {
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #666;
  margin-bottom: 4px;
}

.readiness-status {
  font-size: 18px;
  font-weight: 700;
  margin-bottom: 6px;
}

.readiness-status.ready { color: #2a2; }
.readiness-status.revise_before_submit { color: #a07000; }
.readiness-status.not_ready { color: #900; }

/* Export section */
.export-section {
  margin-top: 24px;
  padding-top: 16px;
  border-top: 1px solid #e0e0e0;
}

.export-heading {
  font-size: 13px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #666;
  margin-bottom: 12px;
}

.export-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 10px;
}

.export-card {
  border: 1px solid #e0e0e0;
  border-radius: 6px;
  padding: 14px 16px;
  cursor: pointer;
  transition: all 0.15s;
  text-align: center;
}

.export-card:hover {
  border-color: #000;
  background: #fafafa;
}

.export-icon {
  font-family: 'JetBrains Mono', monospace;
  font-size: 16px;
  font-weight: 700;
  color: #333;
  margin-bottom: 6px;
}

.export-label {
  font-size: 12px;
  font-weight: 500;
  color: #333;
  margin-bottom: 2px;
}

.export-format {
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #999;
}

/* Graph context section */
.graph-context-section {
  border: 1px solid #e0e0e0;
  border-radius: 6px;
  padding: 16px 20px;
  margin-bottom: 20px;
  background: #fafafa;
}
.collapsible-header {
  display: flex;
  align-items: center;
  gap: 12px;
  cursor: pointer;
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #333;
}
.graph-ctx-stats {
  font-size: 12px;
  font-weight: 400;
  color: #888;
  text-transform: none;
  letter-spacing: 0;
}
.toggle-icon { margin-left: auto; font-size: 16px; color: #999; }
.graph-summary-block {
  margin-top: 12px;
  font-size: 11px;
  line-height: 1.6;
  color: #444;
  white-space: pre-wrap;
  font-family: 'JetBrains Mono', monospace;
  max-height: 500px;
  overflow-y: auto;
  padding: 12px;
  background: #fff;
  border: 1px solid #eee;
  border-radius: 4px;
}

/* Graph Utilization */
.utilization-section {
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  padding: 16px 20px;
  margin-bottom: 24px;
}

.utilization-body { margin-top: 16px; }

.utilization-body h4 {
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #555;
  margin-bottom: 10px;
}

.util-reviewer-bars { margin-bottom: 20px; }

.util-bar-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}

.util-bar-label {
  font-size: 12px;
  font-weight: 600;
  color: #333;
  min-width: 100px;
}

.util-bar-track {
  flex: 1;
  height: 8px;
  background: #eee;
  border-radius: 4px;
  overflow: hidden;
}

.util-bar-fill {
  height: 100%;
  background: #000;
  border-radius: 4px;
  transition: width 0.3s;
}

.util-bar-pct {
  font-size: 12px;
  font-weight: 600;
  color: #888;
  min-width: 35px;
  text-align: right;
}

.util-type-grid { margin-bottom: 20px; }

.util-type-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.util-type-chip {
  font-size: 11px;
  font-weight: 600;
  padding: 3px 10px;
  border-radius: 3px;
  border: 1px solid;
}

.util-type-chip.good { background: #e8f5e9; border-color: #c8e6c9; color: #2a2; }
.util-type-chip.partial { background: #fff8e1; border-color: #fff3b0; color: #a07000; }
.util-type-chip.low { background: #ffebee; border-color: #ffcdd2; color: #900; }

.util-blind-spots { margin-bottom: 10px; }

.blind-spot-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.blind-spot-chip {
  font-size: 11px;
  padding: 2px 8px;
  background: #fff5f5;
  border: 1px solid #ffcdd2;
  border-radius: 3px;
  color: #900;
}

.blind-spot-chip small { color: #cc7777; }

.blind-spot-more {
  font-size: 11px;
  color: #999;
  padding: 2px 8px;
}
</style>
