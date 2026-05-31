import axios from 'axios'

const KERNEL_BASE = import.meta.env.VITE_KERNEL_URL || 'http://localhost:5002'

const kernel = axios.create({
  baseURL: KERNEL_BASE,
  timeout: 600000,
})

export function getHealth() {
  return kernel.get('/api/health')
}

export function getConferences() {
  return kernel.get('/api/apps/paper_review/conferences')
}

export function getConference(slug) {
  return kernel.get(`/api/apps/paper_review/conferences/${slug}`)
}

export function getModels() {
  return kernel.get('/api/models')
}

export function startReview(file, conference, modelMap = {}, maxRounds = 3, userInstructions = '', options = {}) {
  const form = new FormData()
  form.append('file', file)
  form.append('conference', conference)
  form.append('model_map_json', JSON.stringify(modelMap))
  form.append('max_rounds', maxRounds.toString())
  if (userInstructions) form.append('user_instructions', userInstructions)
  if (options.artifactDescriptionAssumedPresent) form.append('artifact_description_assumed_present', 'true')
  return kernel.post('/api/apps/paper_review/review', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

export function getSession(sessionId) {
  return kernel.get(`/api/sessions/${sessionId}`)
}

export function listSessions(limit = 20) {
  return kernel.get('/api/sessions', { params: { limit } })
}

export function getReviewPacket(sessionId) {
  return kernel.get(`/api/apps/paper_review/sessions/${sessionId}/review-packet`)
}

export function connectStream(sessionId) {
  const wsBase = KERNEL_BASE.replace(/^http/, 'ws')
  return new WebSocket(`${wsBase}/api/sessions/${sessionId}/stream`)
}

export function runPreflight(file, conference) {
  const form = new FormData()
  form.append('file', file)
  form.append('conference', conference)
  return kernel.post('/api/apps/paper_review/preflight', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

export function getReviewPacketMd(sessionId) {
  return kernel.get(`/api/apps/paper_review/sessions/${sessionId}/review-packet.md`, {
    responseType: 'blob',
  })
}

export function getReviewPacketPdf(sessionId) {
  return kernel.get(`/api/apps/paper_review/sessions/${sessionId}/review-packet.pdf`, {
    responseType: 'blob',
  })
}

export function getSessionGraph(sessionId) {
  return kernel.get(`/api/sessions/${sessionId}/graph`)
}

export function getGraphAtStep(sessionId, stepName) {
  return kernel.get(`/api/sessions/${sessionId}/graph/step/${stepName}`)
}

export function getGraphSummary(sessionId) {
  return kernel.get(`/api/sessions/${sessionId}/graph-summary`)
}

export function getSessionOntology(sessionId) {
  return kernel.get(`/api/sessions/${sessionId}/ontology`)
}

// Pipeline control
export function getPipelineStatus(sessionId) {
  return kernel.get(`/api/sessions/${sessionId}/pipeline`)
}

export function pipelineAdvance(sessionId) {
  return kernel.post(`/api/sessions/${sessionId}/pipeline/advance`)
}

export function pipelinePause(sessionId) {
  return kernel.post(`/api/sessions/${sessionId}/pipeline/pause`)
}

export function pipelineResume(sessionId) {
  return kernel.post(`/api/sessions/${sessionId}/pipeline/resume`)
}

export function pipelineCancel(sessionId) {
  return kernel.post(`/api/sessions/${sessionId}/pipeline/cancel`)
}

export function pipelineStepRun(sessionId, stepName) {
  return kernel.post(`/api/sessions/${sessionId}/pipeline/step/${stepName}/run`)
}

export function editOntology(sessionId, { editedEntityTypes = null, editedEdgeTypes = null } = {}) {
  return kernel.post(`/api/sessions/${sessionId}/pipeline/edit-ontology`, {
    edited_entity_types: editedEntityTypes,
    edited_edge_types: editedEdgeTypes,
  })
}

export function generateOntology(sessionId, model = 'mini/qwen35-distilled') {
  const form = new FormData()
  form.append('model', model)
  return kernel.post(`/api/sessions/${sessionId}/generate-ontology`, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

export function extractGraph(sessionId, model = 'mini/qwen35-distilled') {
  const form = new FormData()
  form.append('model', model)
  return kernel.post(`/api/sessions/${sessionId}/extract-graph`, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

// Settings persistence
export function getSettings() {
  return kernel.get('/api/settings')
}

export function updateSettings(patch) {
  return kernel.put('/api/settings', patch)
}

export function getActiveModelAssignments() {
  return kernel.get('/api/settings/active-models')
}

// Presets
export function getPresets() {
  return kernel.get('/api/presets')
}

export function activatePreset(name) {
  return kernel.post(`/api/presets/${name}/activate`)
}

// Model discovery and benchmarking
export function discoverModels() {
  return kernel.post('/api/models/discover')
}

export function startBenchmark(modelIds = []) {
  return kernel.post('/api/models/benchmark', { model_ids: modelIds })
}

export function getBenchmarkResults() {
  return kernel.get('/api/models/benchmark')
}

// AI Provider management (OAuth login for subscription services)
export function getProviders() {
  return kernel.get('/api/providers')
}

export function getProviderStatus(providerName) {
  return kernel.get(`/api/providers/${providerName}`)
}

export function beginProviderLogin(provider) {
  return kernel.post('/api/providers/login', { provider })
}

export function getLoginStatus(providerName) {
  return kernel.get(`/api/providers/${providerName}/login-status`)
}

export function completeProviderLogin(provider, code, verifier = '', state = '') {
  return kernel.post('/api/providers/callback', { provider, code, verifier, state })
}

export function providerLogout(providerName) {
  return kernel.post(`/api/providers/${providerName}/logout`)
}

// Batch operations
export function startBatch(files, conference, modelMap = {}) {
  const form = new FormData()
  files.forEach(f => form.append('files', f))
  form.append('conference', conference)
  form.append('model_map_json', JSON.stringify(modelMap))
  return kernel.post('/api/apps/paper_review/batch', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

export function startBatchReview(files, conference, modelMap = {}, maxRounds = 3, userInstructions = '', options = {}) {
  const form = new FormData()
  files.forEach(f => form.append('files', f))
  form.append('conference', conference)
  form.append('model_map_json', JSON.stringify(modelMap))
  form.append('max_rounds', maxRounds.toString())
  if (userInstructions) form.append('user_instructions', userInstructions)
  if (options.artifactDescriptionAssumedPresent) form.append('artifact_description_assumed_present', 'true')
  return kernel.post('/api/apps/paper_review/batch-review', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

export function getBatch(batchId) {
  return kernel.get(`/api/apps/paper_review/batch/${batchId}`)
}

export function listBatches(limit = 20) {
  return kernel.get('/api/apps/paper_review/batches', { params: { limit } })
}

export function retrySession(sessionId) {
  return kernel.post(`/api/apps/paper_review/sessions/${sessionId}/retry`)
}

export function retryFailedInBatch(batchId) {
  return kernel.post(`/api/apps/paper_review/batch/${batchId}/retry-failed`)
}

export function launchReview(sessionId, options = null) {
  return kernel.post(`/api/apps/paper_review/sessions/${sessionId}/launch-review`, options || {})
}

// Post-review: refine field, score lightpass, persist edits
export function refineField(sessionId, field, instruction, currentFields) {
  return kernel.post(`/api/apps/paper_review/sessions/${sessionId}/refine-field`, {
    field, instruction, current_fields: currentFields,
  })
}

export function scoreLightpass(sessionId, newScore, newLabel, currentFields) {
  return kernel.post(`/api/apps/paper_review/sessions/${sessionId}/score-lightpass`, {
    new_score: newScore, new_label: newLabel, current_fields: currentFields,
  })
}

export function updateFinalReview(sessionId, finalReview) {
  return kernel.post(`/api/apps/paper_review/sessions/${sessionId}/update-final-review`, {
    final_review: finalReview,
  })
}

// Graph export/import
export function exportGraph(sessionId) {
  return kernel.get(`/api/sessions/${sessionId}/graph/export`, {
    responseType: 'blob',
  })
}

export function reviewWithGraph(graphFile, conference, modelMap = {}, maxRounds = 3, userInstructions = '', options = {}) {
  const form = new FormData()
  form.append('graph_file', graphFile)
  form.append('conference', conference)
  form.append('model_map_json', JSON.stringify(modelMap))
  form.append('max_rounds', maxRounds.toString())
  if (userInstructions) form.append('user_instructions', userInstructions)
  if (options.artifactDescriptionAssumedPresent) form.append('artifact_description_assumed_present', 'true')
  return kernel.post('/api/apps/paper_review/review-with-graph', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

// Graph utilization
export function getGraphUtilization(sessionId) {
  return kernel.get(`/api/sessions/${sessionId}/graph-utilization`)
}

// Export
export function getExportFormats() {
  return kernel.get('/api/export/formats')
}

export function exportSession(sessionId, format = 'json') {
  return kernel.get(`/api/sessions/${sessionId}/export`, {
    params: { format },
    responseType: 'blob',
  })
}

// Parsers and tools
export function getParsers() {
  return kernel.get('/api/parsers')
}

export function getTools() {
  return kernel.get('/api/tools')
}

// App manifests
export function getManifests() {
  return kernel.get('/api/manifests')
}

export function getManifest(appName) {
  return kernel.get(`/api/manifests/${appName}`)
}

export default kernel
