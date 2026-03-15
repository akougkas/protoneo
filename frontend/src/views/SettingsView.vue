<template>
  <div class="settings-page">
    <header class="settings-header">
      <div class="header-left">
        <router-link to="/" class="logo">ProtoNeo</router-link>
        <span class="product-tag">Settings</span>
      </div>
      <router-link to="/" class="back-link">Back to Reviews</router-link>
    </header>

    <!-- No quality banner here - that belongs in PanelHome, not Settings -->

    <!-- ═══════════════ AI PROVIDERS ═══════════════ -->
    <section class="section">
      <div class="section-header-row">
        <div>
          <h2 class="section-title">AI Providers</h2>
          <p class="section-desc">{{ activeProviderCount }} providers connected, {{ activeModelCount }} models available.</p>
        </div>
        <button class="action-btn-sm" @click="refreshDiscovery" :disabled="discovering">
          {{ discovering ? 'Scanning...' : 'Refresh All' }}
        </button>
      </div>

      <!-- ─── Localhost ─── -->
      <div class="tier-group">
        <div class="tier-label-row">
          <span class="tier-dot local"></span>
          <h3 class="tier-name">Localhost</h3>
          <span class="tier-hint">Services on this machine (LM Studio, Ollama)</span>
        </div>
        <div class="provider-grid">
          <div v-for="node in localNodes" :key="node.id" :class="['provider-card', { connected: node.online, disabled: !isProviderEnabled(node.id) }]">
            <div class="pc-top">
              <div class="pc-name-row">
                <span :class="['status-dot', node.online ? 'on' : 'off']"></span>
                <span class="pc-name">{{ node.display_name || node.id }}</span>
                <span class="pc-type-badge">{{ node.type === 'ollama' ? 'Ollama' : 'OpenAI-compat' }}</span>
              </div>
              <div class="pc-controls">
                <label class="power-toggle">
                  <input type="checkbox" :checked="isProviderEnabled(node.id)" @change="toggleEndpoint(node.id, $event.target.checked)" />
                  <span class="power-slider"></span>
                </label>
                <span :class="['pc-status-label', node.online && isProviderEnabled(node.id) ? 'connected' : '']">
                  {{ !isProviderEnabled(node.id) ? 'Off' : node.online ? (node.models?.length || 0) + ' discovered' : 'Offline' }}
                </span>
              </div>
            </div>
            <div class="pc-detail mono">{{ node.url }}</div>

            <!-- Loaded vs available distinction -->
            <div v-if="node.online && node.loaded_model" class="pc-loaded">
              In VRAM: <strong>{{ node.loaded_model }}</strong>
            </div>
            <div v-if="node.online && node.loaded_models?.length" class="pc-loaded">
              In memory: <strong>{{ node.loaded_models.join(', ') }}</strong>
            </div>

            <!-- Active model selection -->
            <div v-if="node.online && node.models?.length" class="pc-select">
              <label class="select-label">Active model:</label>
              <select class="provider-model-select" :disabled="!isProviderEnabled(node.id)" :value="settings.active_models[node.id] || ''" @change="setActiveModel(node.id, $event.target.value)">
                <option value="">Select a model...</option>
                <option v-for="m in node.models" :key="m.id" :value="m.id">
                  {{ m.id }}{{ m.loaded ? ' [loaded]' : '' }}{{ m.context_length ? ` (${formatContext(m.context_length)} ctx)` : '' }}
                </option>
              </select>
            </div>
            <div v-if="selectedModelMeta(node.id)" class="selected-model-meta">
              <span v-if="selectedModelMeta(node.id).context_length" class="meta-chip">{{ formatContext(selectedModelMeta(node.id).context_length) }} ctx</span>
              <span v-if="selectedModelMeta(node.id).loaded" class="meta-chip live">loaded</span>
              <span v-if="selectedModelMeta(node.id).speed" class="meta-chip speed">{{ selectedModelMeta(node.id).speed }} t/s</span>
              <span v-if="selectedModelMeta(node.id).benchmark" :class="['class-chip', selectedModelMeta(node.id).benchmark.protoneo_class]">
                {{ selectedModelMeta(node.id).benchmark.protoneo_class }}
              </span>
            </div>
            <div v-if="selectedModelMeta(node.id)?.loaded === false" class="pc-nudge">Selected model is not loaded in VRAM. Load it in the service, then refresh discovery.</div>

            <!-- Nudge when no models -->
            <div v-if="node.online && !node.models?.length && node.nudge" class="pc-nudge">{{ node.nudge }}</div>
            <div v-if="!node.online" class="pc-nudge">
              Service not running. Start LM Studio or Ollama on this machine to use local models.
            </div>
          </div>
        </div>
      </div>

      <!-- ─── Homelab (LAN) ─── -->
      <div class="tier-group" v-if="lanNodes.length">
        <div class="tier-label-row">
          <span class="tier-dot homelab"></span>
          <h3 class="tier-name">Homelab (LAN)</h3>
          <span class="tier-hint">Configured LAN endpoints</span>
        </div>
        <div class="provider-grid">
          <div v-for="node in lanNodes" :key="node.id" :class="['provider-card', { connected: node.online, disabled: !isProviderEnabled(node.id) }]">
            <div class="pc-top">
              <div class="pc-name-row">
                <span :class="['status-dot', node.online ? 'on' : 'off']"></span>
                <span class="pc-name">{{ node.display_name || node.id }}</span>
              </div>
              <div class="pc-controls">
                <label class="power-toggle">
                  <input type="checkbox" :checked="isProviderEnabled(node.id)" @change="toggleEndpoint(node.id, $event.target.checked)" />
                  <span class="power-slider"></span>
                </label>
                <span :class="['pc-status-label', node.online && isProviderEnabled(node.id) ? 'connected' : '']">
                  {{ !isProviderEnabled(node.id) ? 'Off' : node.online ? (node.models?.length || 0) + ' discovered' : 'Offline' }}
                </span>
              </div>
            </div>
            <div class="pc-detail mono">{{ node.url }}</div>
            <div v-if="node.online && node.loaded_model" class="pc-loaded">In VRAM: <strong>{{ node.loaded_model }}</strong></div>
            <div v-if="node.online && node.models?.length" class="pc-select">
              <label class="select-label">Active model:</label>
              <select class="provider-model-select" :disabled="!isProviderEnabled(node.id)" :value="settings.active_models[node.id] || ''" @change="setActiveModel(node.id, $event.target.value)">
                <option value="">Select a model...</option>
                <option v-for="m in node.models" :key="m.id" :value="m.id">
                  {{ m.id }}{{ m.loaded ? ' [loaded]' : '' }}{{ m.context_length ? ` (${formatContext(m.context_length)} ctx)` : '' }}
                </option>
              </select>
            </div>
            <div v-if="selectedModelMeta(node.id)" class="selected-model-meta">
              <span v-if="selectedModelMeta(node.id).context_length" class="meta-chip">{{ formatContext(selectedModelMeta(node.id).context_length) }} ctx</span>
              <span v-if="selectedModelMeta(node.id).loaded" class="meta-chip live">loaded</span>
              <span v-if="selectedModelMeta(node.id).speed" class="meta-chip speed">{{ selectedModelMeta(node.id).speed }} t/s</span>
              <span v-if="selectedModelMeta(node.id).benchmark" :class="['class-chip', selectedModelMeta(node.id).benchmark.protoneo_class]">
                {{ selectedModelMeta(node.id).benchmark.protoneo_class }}
              </span>
            </div>
            <div v-if="selectedModelMeta(node.id)?.loaded === false" class="pc-nudge">Selected model is not loaded in VRAM. Load it on this endpoint, then refresh discovery.</div>
            <div v-if="node.nudge" class="pc-nudge">{{ node.nudge }}</div>
          </div>
        </div>
      </div>

      <!-- ─── API-based ─── -->
      <div class="tier-group">
        <div class="tier-label-row">
          <span class="tier-dot api"></span>
          <h3 class="tier-name">API-based</h3>
          <span class="tier-hint">Cloud endpoints with API keys</span>
        </div>
        <div class="provider-grid">
          <div :class="['provider-card', { connected: openrouterAvailable, disabled: !isProviderEnabled('openrouter') }]">
            <div class="pc-top">
              <div class="pc-name-row">
                <span :class="['status-dot', openrouterAvailable ? 'on' : 'off']"></span>
                <span class="pc-name">OpenRouter</span>
              </div>
              <div class="pc-controls">
                <label class="power-toggle">
                  <input type="checkbox" :checked="isProviderEnabled('openrouter')" @change="toggleProvider('openrouter', $event.target.checked)" />
                  <span class="power-slider"></span>
                </label>
                <span :class="['pc-status-label', openrouterAvailable && isProviderEnabled('openrouter') ? 'connected' : '']">
                  {{ !isProviderEnabled('openrouter') ? 'Off' : openrouterAvailable ? openrouterModelCount + ' models' : 'Not configured' }}
                </span>
              </div>
            </div>
            <div class="pc-toggle-row">
              <label class="toggle-label">
                <input type="checkbox" v-model="settings.openrouter_free_only" @change="saveAndRefresh" />
                Free tier only
              </label>
              <span class="toggle-hint" v-if="discovery.openrouter?.total_available">
                ({{ discovery.openrouter.total_available }} total)
              </span>
            </div>
            <div v-if="openrouterAvailable && openrouterModelCount > 0" class="pc-select">
              <label class="select-label">Active model:</label>
              <select class="provider-model-select" :disabled="!isProviderEnabled('openrouter')" :value="settings.active_models['openrouter'] || ''" @change="setActiveModel('openrouter', $event.target.value)">
                <option value="">Select a model...</option>
                <option v-for="m in (discovery.openrouter?.models || [])" :key="m.id" :value="m.id">{{ m.name || m.id }}</option>
              </select>
            </div>
            <div v-if="selectedModelMeta('openrouter')" class="selected-model-meta">
              <span v-if="selectedModelMeta('openrouter').context_length" class="meta-chip">{{ formatContext(selectedModelMeta('openrouter').context_length) }} ctx</span>
              <span v-if="selectedModelMeta('openrouter').speed" class="meta-chip speed">{{ selectedModelMeta('openrouter').speed }} t/s</span>
              <span v-if="selectedModelMeta('openrouter').benchmark" :class="['class-chip', selectedModelMeta('openrouter').benchmark.protoneo_class]">
                {{ selectedModelMeta('openrouter').benchmark.protoneo_class }}
              </span>
            </div>
            <div v-if="!openrouterAvailable" class="pc-nudge">Set OPENROUTER_API_KEY in .env to access cloud models.</div>
          </div>
        </div>
      </div>

      <!-- ─── Subscriptions ─── -->
      <div class="tier-group">
        <div class="tier-label-row">
          <span class="tier-dot subscription"></span>
          <h3 class="tier-name">Subscriptions</h3>
          <span class="tier-hint">OAuth login to Claude Max, ChatGPT, Gemini</span>
        </div>
        <div class="provider-grid">
          <div v-for="p in subscriptionProviders" :key="p.provider" :class="['provider-card', { connected: p.logged_in || p.has_credentials, disabled: !isProviderEnabled(p.provider) }]">
            <div class="pc-top">
              <div class="pc-name-row">
                <span :class="['status-dot', p.logged_in || p.has_credentials ? 'on' : 'off']"></span>
                <span class="pc-name">{{ p.display_name }}</span>
                <span v-if="p.oauth_experimental" class="pc-type-badge experimental">Experimental</span>
              </div>
              <div class="pc-controls">
                <label class="power-toggle">
                  <input type="checkbox" :checked="isProviderEnabled(p.provider)" @change="toggleProvider(p.provider, $event.target.checked)" />
                  <span class="power-slider"></span>
                </label>
                <span :class="['pc-status-label', (p.logged_in || p.has_credentials) && isProviderEnabled(p.provider) ? 'connected' : '']">
                  {{ !isProviderEnabled(p.provider) ? 'Off' : p.logged_in ? (p.token_type === 'oauth' ? 'OAuth' : 'Connected') : p.has_credentials ? 'API Key' : 'Not connected' }}
                </span>
              </div>
            </div>

            <div v-if="p.email" class="pc-detail">{{ p.email }}</div>
            <div v-if="p.logged_in && !p.expired" class="pc-detail">Token expires {{ formatExpiry(p.expires_at) }}</div>

            <!-- Active model from discovered -->
            <div v-if="discoveredProviderModels(p.provider).length" class="pc-select">
              <label class="select-label">Active model:</label>
              <select class="provider-model-select" :disabled="!isProviderEnabled(p.provider)" :value="settings.active_models[p.provider] || ''" @change="setActiveModel(p.provider, $event.target.value)">
                <option value="">Select a model...</option>
                <option v-for="m in discoveredProviderModels(p.provider)" :key="m.id" :value="m.id">{{ m.name || m.id }}</option>
              </select>
            </div>
            <!-- Manual model ID input when discovery returns empty but provider is connected -->
            <div v-else-if="(p.logged_in || p.has_credentials) && isProviderEnabled(p.provider)" class="pc-select">
              <label class="select-label">Model ID:</label>
              <div class="manual-model-row">
                <input
                  class="manual-model-input"
                  :placeholder="p.provider === 'openai' ? 'e.g. gpt-4.1' : p.provider.startsWith('google') ? 'e.g. gemini-2.5-pro' : 'e.g. claude-sonnet-4-6'"
                  :value="settings.active_models[p.provider] || ''"
                  @change="setActiveModel(p.provider, $event.target.value.trim())"
                  @keydown.enter="setActiveModel(p.provider, $event.target.value.trim())"
                  :disabled="!isProviderEnabled(p.provider)"
                />
              </div>
            </div>
            <div v-if="selectedModelMeta(p.provider)" class="selected-model-meta">
              <span v-if="selectedModelMeta(p.provider).context_length" class="meta-chip">{{ formatContext(selectedModelMeta(p.provider).context_length) }} ctx</span>
              <span v-if="selectedModelMeta(p.provider).speed" class="meta-chip speed">{{ selectedModelMeta(p.provider).speed }} t/s</span>
              <span v-if="selectedModelMeta(p.provider).benchmark" :class="['class-chip', selectedModelMeta(p.provider).benchmark.protoneo_class]">
                {{ selectedModelMeta(p.provider).benchmark.protoneo_class }}
              </span>
            </div>

            <!-- Nudge from discovery -->
            <div v-if="discoveryNudge(p.provider)" class="pc-nudge">{{ discoveryNudge(p.provider) }}</div>
            <div v-else-if="p.connection_hint" class="pc-nudge">{{ p.connection_hint }}</div>

            <!-- Login flow -->
            <div v-if="pendingLogin === p.provider" class="pc-login-progress">
              <div v-if="p.provider !== 'anthropic'" class="pc-waiting"><span class="waiting-dot"></span> Waiting for browser login...</div>
              <div v-if="p.provider === 'anthropic' || showManualPaste" class="pc-paste">
                <label class="paste-label">{{ p.provider === 'anthropic' ? 'Paste the code#state:' : 'Paste redirect URL:' }}</label>
                <div class="paste-row">
                  <input v-model="pasteCode" class="paste-input" @keydown.enter="submitPasteCode(p.provider)" />
                  <button class="paste-btn" @click="submitPasteCode(p.provider)" :disabled="!pasteCode">Submit</button>
                </div>
              </div>
            </div>

            <div class="pc-actions">
              <button v-if="p.oauth_enabled !== false && !p.logged_in && !p.has_credentials" class="connect-btn" :disabled="pendingLogin === p.provider" @click="startLogin(p.provider)">
                {{ pendingLogin === p.provider ? 'Waiting...' : 'Connect' }}
              </button>
              <button v-if="p.logged_in" class="disconnect-btn" @click="disconnect(p.provider)">Disconnect</button>
            </div>
            <div v-if="loginError && pendingLogin === p.provider" class="pc-error">{{ loginError }}</div>
          </div>
        </div>
      </div>
    </section>

    <!-- ═══════════════ ACTIVE MODELS ═══════════════ -->
    <section class="section">
      <div class="section-header-row">
        <div>
          <h2 class="section-title">Active Models</h2>
          <p class="section-desc">Models selected as defaults across your providers. Select models above, then score them here.</p>
        </div>
        <button class="action-btn-sm bench-btn" @click="runBenchmark" :disabled="benchmarking || !activeModelList.length">
          {{ benchmarking ? `Scoring ${benchProgress}...` : 'Score Model Capabilities' }}
        </button>
      </div>

      <div v-if="!activeModelList.length" class="empty-state">
        No models selected. Use the "Active model" dropdowns above to choose a default model per provider.
      </div>

      <div v-else class="model-table-wrap">
        <table class="model-table">
          <thead>
            <tr>
              <th>Provider</th>
              <th>Model</th>
              <th class="num">Context</th>
              <th class="num">Speed</th>
              <th>Tags</th>
              <th v-if="hasBench" class="score-band-col">5D Score</th>
              <th v-if="hasBench" class="num">Total</th>
              <th v-if="hasBench">Class</th>
              <th v-if="hasBench">Suggested Roles</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="m in activeModelRows" :key="m.provider + '/' + m.id" class="model-row">
              <td><strong :title="m.provider">{{ providerLabel(m.provider) }}</strong></td>
              <td class="mono model-id-cell" :title="m.id">{{ m.name || m.id }}</td>
              <td class="num mono">{{ m.context_length ? formatContext(m.context_length) : '--' }}</td>
              <td class="num mono">{{ benchThroughput(m.provider, m.id) }}</td>
              <td>
                <div class="cap-chips">
                  <template v-if="m.benchmark?.tags?.length">
                    <span v-for="tag in m.benchmark.tags" :key="tag" :class="['cap-chip', tagClass(tag)]">{{ tag }}</span>
                  </template>
                  <span v-else class="cap-hint">Run benchmark</span>
                </div>
              </td>
              <template v-if="hasBench">
                <td class="score-band-cell">
                  <div class="score-band">
                    <div v-for="dim in m.dimensionScores" :key="dim.key" :class="['score-band-item', dimScoreClass(dim.score)]">
                      <span class="score-band-label">{{ dimLabels[dim.key] }}</span>
                      <span class="score-band-value">{{ dim.score ?? '--' }}</span>
                    </div>
                  </div>
                </td>
                <td class="num">
                  <span v-if="m.benchmark" :class="['bench-score', benchClass(m.benchmark.total_score)]">{{ m.benchmark.total_score }}/100</span>
                  <span v-else class="bench-na">--</span>
                </td>
                <td>
                  <span v-if="m.benchmark" :class="['class-chip', m.benchmark.protoneo_class]">{{ m.benchmark.protoneo_class }}</span>
                </td>
                <td>
                  <span v-if="m.benchmark?.suggested_roles?.length" class="suggested-roles">
                    {{ m.benchmark.suggested_roles.join(', ') }}
                  </span>
                </td>
              </template>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Benchmark dimension breakdown -->
      <div v-if="benchmarkResults.length" class="bench-details">
        <h3 class="bench-toggle" @click="showBenchDetails = !showBenchDetails">
          Dimension Breakdown {{ showBenchDetails ? '\u25B4' : '\u25BE' }}
        </h3>
        <div v-if="showBenchDetails" class="bench-grid">
          <div v-for="r in benchmarkResults" :key="`${r.provider}/${r.model_id}`" class="bench-card">
            <div class="bench-card-top">
              <span class="mono bench-card-id">{{ r.provider }}/{{ r.model_id }}</span>
              <span :class="['class-chip', r.protoneo_class]">{{ r.protoneo_class }}</span>
            </div>
            <div class="dim-bars">
              <div v-for="(dim, key) in (r.dimensions || {})" :key="key" class="dim-bar-row">
                <span class="dim-bar-label">{{ dimLabels[key] || key }}</span>
                <div class="dim-bar-track">
                  <div class="dim-bar-fill" :style="{width: (dim.score/20*100)+'%'}" :class="dimScoreClass(dim.score)"></div>
                </div>
                <span class="dim-bar-val">{{ dim.score }}/20</span>
              </div>
            </div>
            <div class="bench-throughput">
              {{ r.throughput?.tokens_per_second || 0 }} t/s | {{ r.throughput?.total_completion_tokens || 0 }} tokens | {{ r.throughput?.total_latency_seconds || 0 }}s total
            </div>
            <div v-if="r.tags?.length" class="bench-tags">
              <span v-for="tag in r.tags" :key="tag" :class="['cap-chip', tagClass(tag)]">{{ tag }}</span>
            </div>
            <div v-if="r.error" class="bench-error">{{ r.error }}</div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import {
  getProviders, getSettings, updateSettings, discoverModels,
  startBenchmark, getBenchmarkResults,
  beginProviderLogin, getLoginStatus, completeProviderLogin, providerLogout,
} from '../api/kernel.js'

const providers = ref([])
const discovery = ref({})
const settings = reactive({
  localhost_endpoints: [],
  lan_endpoints: [],
  openrouter_free_only: true,
  provider_enabled: {},
  active_models: {},
  benchmark_results: [],
  discovered_models: {},
})

const benchmarkResults = ref([])
const showBenchDetails = ref(false)
const discovering = ref(false)
const benchmarking = ref(false)
const benchProgress = ref('')

const pendingLogin = ref(null)
const loginError = ref('')
const pasteCode = ref('')
const showManualPaste = ref(false)

let pollTimer = null
let manualPasteTimer = null
let benchPollTimer = null
let loginVerifier = ''
let loginState = ''

// Computed
const subscriptionProviders = computed(() =>
  providers.value.filter(p => ['anthropic', 'openai', 'google', 'google-antigravity'].includes(p.provider))
)
const openrouterAvailable = computed(() => providers.value.find(p => p.provider === 'openrouter')?.has_credentials ?? false)
const openrouterModelCount = computed(() => discovery.value.openrouter?.models?.length || 0)

const localNodes = computed(() => {
  // Always show configured endpoints, enriched with discovery data.
  // This ensures cards never disappear when toggled off or offline.
  const discovered = Array.isArray(discovery.value.localhost) ? discovery.value.localhost : []
  const discoveredById = Object.fromEntries(discovered.map(n => [n.id, n]))
  return (settings.localhost_endpoints || []).map(ep => ({
    id: ep.id,
    display_name: ep.display_name || ep.id,
    url: ep.url,
    type: ep.type || 'openai',
    enabled: ep.enabled !== false,
    online: !!discoveredById[ep.id]?.online,
    models: discoveredById[ep.id]?.models || [],
    loaded_model: discoveredById[ep.id]?.loaded_model || '',
    loaded_models: discoveredById[ep.id]?.loaded_models || [],
    nudge: discoveredById[ep.id]?.nudge || '',
  }))
})
const lanNodes = computed(() => {
  const discovered = Array.isArray(discovery.value.lan) ? discovery.value.lan : []
  const discoveredById = Object.fromEntries(discovered.map(n => [n.id, n]))
  return (settings.lan_endpoints || []).map(ep => ({
    id: ep.id,
    display_name: ep.display_name || ep.id,
    url: ep.url,
    type: ep.type || 'openai',
    enabled: ep.enabled !== false,
    online: !!discoveredById[ep.id]?.online,
    models: discoveredById[ep.id]?.models || [],
    loaded_model: discoveredById[ep.id]?.loaded_model || '',
    loaded_models: discoveredById[ep.id]?.loaded_models || [],
    nudge: discoveredById[ep.id]?.nudge || '',
  }))
})

const activeProviderCount = computed(() => {
  let count = 0
  if (localNodes.value.some(n => n.online && isProviderEnabled(n.id))) count++
  if (lanNodes.value.some(n => n.online && isProviderEnabled(n.id))) count++
  if (openrouterAvailable.value && isProviderEnabled('openrouter')) count++
  for (const p of subscriptionProviders.value) {
    if ((p.logged_in || p.has_credentials) && isProviderEnabled(p.provider)) count++
  }
  return count
})

const activeModelCount = computed(() => {
  let count = 0
  for (const node of [...localNodes.value, ...lanNodes.value]) {
    if (node.online && isProviderEnabled(node.id)) count += node.models?.length || 0
  }
  if (isProviderEnabled('openrouter') && discovery.value.openrouter?.models) count += discovery.value.openrouter.models.length
  for (const prov of ['anthropic', 'openai', 'google', 'google-antigravity']) {
    if (isProviderEnabled(prov) && discovery.value[prov]?.models) count += discovery.value[prov].models.length
  }
  return count
})

const benchIndex = computed(() => {
  const index = {}
  for (const result of benchmarkResults.value) {
    index[benchKey(result.provider, result.model_id)] = result
  }
  return index
})

const activeModelList = computed(() => {
  const list = []
  for (const [provider, modelId] of Object.entries(settings.active_models)) {
    if (!modelId || !isProviderEnabled(provider)) continue
    const model = findDiscoveredModel(provider, modelId)
    list.push({
      provider,
      id: modelId,
      name: model?.name || modelId,
      context_length: model?.context_length || null,
      is_free: model?.is_free ?? null,
      cost_prompt: model?.cost_prompt ?? null,
      provider_type: model?.provider_type || 'local',
      loaded: model?.loaded ?? null,
      temperature: model?.temperature ?? null,
      flash_attention: model?.flash_attention ?? null,
    })
  }
  return list
})

const activeModelRows = computed(() =>
  activeModelList.value.map(model => ({
    ...model,
    benchmark: getBenchResult(model.provider, model.id),
    dimensionScores: dimKeys.map(key => ({
      key,
      score: getBenchDim(model.provider, model.id, key),
    })),
  }))
)

function findConfiguredEndpoint(providerName) {
  return [...settings.localhost_endpoints, ...settings.lan_endpoints].find(ep => ep.id === providerName) || null
}

function providerLabel(providerName) {
  const endpoint = findConfiguredEndpoint(providerName)
  if (endpoint) return endpoint.display_name || endpoint.id
  return providers.value.find(p => p.provider === providerName)?.display_name || providerName
}

function isProviderEnabled(providerName) {
  const endpoint = findConfiguredEndpoint(providerName)
  if (endpoint) return endpoint.enabled !== false
  return settings.provider_enabled[providerName] ?? true
}

function findDiscoveredModel(provider, modelId) {
  // Check local/homelab nodes
  for (const node of [...localNodes.value, ...lanNodes.value]) {
    if (node.id === provider && node.models) {
      const m = node.models.find(m => m.id === modelId)
      if (m) return m
    }
  }
  // Check cloud providers
  const d = discovery.value[provider]
  if (d?.models) {
    return d.models.find(m => m.id === modelId)
  }
  return null
}

function discoveredProviderModels(providerName) {
  const d = discovery.value[providerName]
  return d?.models || []
}

function discoveryNudge(providerName) {
  const d = discovery.value[providerName]
  return d?.nudge || ''
}

function selectedModelMeta(provider) {
  const modelId = settings.active_models[provider]
  if (!modelId) return null
  const model = findDiscoveredModel(provider, modelId)
  const benchmark = getBenchResult(provider, modelId)
  return {
    model,
    context_length: model?.context_length || null,
    loaded: model?.loaded ?? null,
    speed: benchmark?.throughput?.tokens_per_second || null,
    benchmark,
  }
}

function setActiveModel(provider, modelId) {
  settings.active_models[provider] = modelId
  saveSettings()
}

async function toggleEndpoint(provider, enabled) {
  const endpoint = findConfiguredEndpoint(provider)
  if (!endpoint) return
  endpoint.enabled = enabled
  await saveAndRefresh()
}

async function toggleProvider(provider, enabled) {
  settings.provider_enabled[provider] = enabled
  await saveSettings()
}

function formatContext(n) {
  if (!n) return '--'
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(0) + 'M'
  if (n >= 1_000) return (n / 1_000).toFixed(0) + 'K'
  return String(n)
}

function formatModelCost(m) {
  if (m.is_free) return 'free'
  if (m.cost_prompt !== undefined) return m.cost_prompt === 0 ? 'free' : `$${(m.cost_prompt * 1e6).toFixed(1)}/M`
  return '--'
}

function formatExpiry(ts) {
  if (!ts) return ''
  const diffMs = new Date(ts * 1000) - new Date()
  if (diffMs < 0) return 'expired'
  const h = Math.floor(diffMs / 3600000)
  const m = Math.floor((diffMs % 3600000) / 60000)
  if (h > 24) return `in ${Math.floor(h / 24)}d`
  if (h > 0) return `in ${h}h ${m}m`
  return `in ${m}m`
}

const dimKeys = ['json_compliance', 'review_depth', 'reasoning', 'context_utilization', 'instruction_following']
const dimLabels = {
  json_compliance: 'JSON', review_depth: 'Depth', reasoning: 'Reason',
  context_utilization: 'Context', instruction_following: 'Instruct',
}
const hasBench = computed(() => benchmarkResults.value.length > 0)

function benchKey(provider, modelId) {
  return `${provider}/${modelId}`
}
function getBenchResult(provider, modelId) {
  return benchIndex.value[benchKey(provider, modelId)] || null
}
function getBenchDim(provider, modelId, dimKey) {
  const r = getBenchResult(provider, modelId)
  return r?.dimensions?.[dimKey]?.score ?? null
}
function benchThroughput(provider, modelId) {
  const r = getBenchResult(provider, modelId)
  const tps = r?.throughput?.tokens_per_second
  return tps ? `${tps} t/s` : '--'
}
function benchClass(score) { return score >= 80 ? 'high' : score >= 50 ? 'mid' : 'low' }
function dimScoreClass(score) { return score >= 16 ? 'high' : score >= 10 ? 'mid' : 'low' }
function tagClass(tag) {
  const map = { 'structured': 'json', 'deep-review': 'depth', 'reasoning': 'reason', 'long-context': 'context', 'precise': 'instruct' }
  return map[tag] || 'default'
}

// Actions
async function loadAll() {
  try {
    const [pRes, sRes, bRes] = await Promise.all([getProviders(), getSettings(), getBenchmarkResults()])
    providers.value = pRes.data.providers || []
    Object.assign(settings, sRes.data)
    benchmarkResults.value = bRes.data.results || []
  } catch (e) { console.error('Load failed:', e) }
}

async function refreshDiscovery() {
  discovering.value = true
  try { discovery.value = (await discoverModels()).data || {} }
  catch (e) { console.error('Discovery failed:', e) }
  finally { discovering.value = false }
}

async function saveSettings() {
  try {
    await updateSettings({
      localhost_endpoints: settings.localhost_endpoints,
      lan_endpoints: settings.lan_endpoints,
      provider_enabled: settings.provider_enabled,
      active_models: settings.active_models,
      openrouter_free_only: settings.openrouter_free_only,
    })
  }
  catch (e) { console.error('Save failed:', e) }
}

async function saveAndRefresh() { await saveSettings(); await refreshDiscovery() }

async function runBenchmark() {
  if (!activeModelList.value.length) return
  benchmarking.value = true
  benchmarkResults.value = []
  const ids = activeModelList.value.map(m => `${m.provider}/${m.id}`)
  try {
    const res = await startBenchmark(ids)
    benchProgress.value = `0/${res.data.model_count}`
    benchPollTimer = setInterval(async () => {
      const bRes = await getBenchmarkResults()
      benchmarkResults.value = bRes.data.results || []
      benchProgress.value = `${benchmarkResults.value.length}/${res.data.model_count}`
      if (!bRes.data.running) { clearInterval(benchPollTimer); benchmarking.value = false; showBenchDetails.value = true }
    }, 2000)
  } catch (e) { benchmarking.value = false }
}

async function startLogin(prov) {
  loginError.value = ''; pasteCode.value = ''; pendingLogin.value = prov
  try {
    const res = await beginProviderLogin(prov)
    loginVerifier = res.data.verifier; loginState = res.data.state
    window.open(res.data.url, '_blank')
    if (res.data.needs_local_server) {
      startPolling(prov)
      showManualPaste.value = false
      manualPasteTimer = setTimeout(() => { showManualPaste.value = true }, 10000)
    }
  } catch (e) { loginError.value = e.response?.data?.detail || 'Login failed'; pendingLogin.value = null }
}

function startPolling(prov) {
  pollTimer = setInterval(async () => {
    const res = await getLoginStatus(prov)
    if (res.data.status === 'complete') { stopPolling(); pendingLogin.value = null; await loadAll(); await refreshDiscovery() }
    else if (res.data.status === 'error' || res.data.status === 'timeout') { stopPolling(); loginError.value = res.data.error || 'Failed'; pendingLogin.value = null }
  }, 2000)
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
  if (manualPasteTimer) { clearTimeout(manualPasteTimer); manualPasteTimer = null }
  showManualPaste.value = false
}

async function submitPasteCode(prov) {
  if (!pasteCode.value) return; loginError.value = ''
  try { await completeProviderLogin(prov, pasteCode.value, loginVerifier, loginState); pendingLogin.value = null; pasteCode.value = ''; stopPolling(); await loadAll(); await refreshDiscovery() }
  catch (e) { loginError.value = e.response?.data?.detail || 'Failed' }
}

async function disconnect(prov) { await providerLogout(prov); await loadAll(); await refreshDiscovery() }

onMounted(async () => { await loadAll(); await refreshDiscovery() })
onUnmounted(() => { stopPolling(); if (benchPollTimer) clearInterval(benchPollTimer) })
</script>

<style scoped>
.settings-page {
  max-width: 1240px;
  margin: 0 auto;
  padding: 40px 24px 56px;
}
.settings-header { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 24px; }
.header-left { display: flex; align-items: baseline; gap: 12px; }
.logo { font-family: 'Space Grotesk', sans-serif; font-size: 28px; font-weight: 700; letter-spacing: -0.5px; text-decoration: none; color: inherit; }
.product-tag { font-size: 13px; font-weight: 500; background: #000; color: #fff; padding: 2px 10px; border-radius: 3px; }
.back-link { font-size: 13px; color: #666; text-decoration: none; }
.back-link:hover { color: #000; }

.section { margin-bottom: 48px; }
.section-header-row { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 20px; gap: 16px; }
.section-title { font-size: 14px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; color: #333; margin-bottom: 4px; }
.section-desc { font-size: 13px; color: #888; }
.action-btn-sm { font-size: 12px; font-weight: 600; padding: 6px 16px; border: 1px solid #ddd; background: #fff; color: #333; border-radius: 4px; cursor: pointer; }
.action-btn-sm:hover { border-color: #000; }
.action-btn-sm:disabled { opacity: 0.5; cursor: not-allowed; }
.bench-btn { border-color: #000; background: #000; color: #fff; }
.bench-btn:hover { background: #222; }

.tier-group { margin-bottom: 24px; }
.tier-label-row { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; padding-bottom: 6px; border-bottom: 1px solid #f0f0f0; }
.tier-dot { width: 10px; height: 10px; border-radius: 50%; }
.tier-dot.local { background: #4a4; }
.tier-dot.homelab { background: #999; }
.tier-dot.api { background: #e8a500; }
.tier-dot.subscription { background: #999; }
.tier-name { font-size: 13px; font-weight: 700; color: #333; text-transform: uppercase; letter-spacing: 0.3px; }
.tier-hint { font-size: 11px; color: #aaa; }

.provider-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; }
.provider-card {
  border: 1px solid #e0e0e0;
  border-radius: 6px;
  padding: 16px 18px;
  background: #fff;
  transition: opacity 0.2s ease, border-color 0.2s ease;
}
.provider-card.connected { border-color: #e0e0e0; }
.provider-card.disabled { opacity: 0.62; }
.pc-top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.pc-controls { display: flex; align-items: center; gap: 10px; }
.pc-name-row { display: flex; align-items: center; gap: 8px; }
.status-dot { width: 8px; height: 8px; border-radius: 50%; }
.status-dot.on { background: #4a4; }
.status-dot.off { border: 2px solid #ccc; width: 4px; height: 4px; }
.pc-name { font-size: 15px; font-weight: 700; color: #111; }
.pc-type-badge { font-size: 9px; font-weight: 600; padding: 1px 5px; border-radius: 2px; background: #f0f0f0; color: #888; text-transform: uppercase; }
.pc-type-badge.experimental { background: #fff3e0; color: #b45309; }
.pc-status-label { font-size: 11px; font-weight: 600; color: #999; text-transform: uppercase; letter-spacing: 0.3px; }
.pc-status-label.connected { color: #4a4; }
.pc-detail { font-size: 12px; color: #888; margin-bottom: 4px; }
.pc-detail.mono, .mono { font-family: 'JetBrains Mono', monospace; font-size: 11px; }
.pc-loaded { font-size: 12px; color: #333; margin: 6px 0; padding: 4px 8px; background: #f0f8f0; border-radius: 3px; border-left: 3px solid #4a4; }
.pc-select { margin: 8px 0; }
.select-label { font-size: 11px; color: #666; display: block; margin-bottom: 4px; }
.provider-model-select { width: 100%; padding: 6px 8px; border: 1px solid #ddd; border-radius: 4px; font-family: 'JetBrains Mono', monospace; font-size: 11px; background: #fff; color: #333; }
.provider-model-select:focus { outline: none; border-color: #000; }
.manual-model-row { display: flex; gap: 6px; }
.manual-model-input { flex: 1; padding: 6px 8px; border: 1px solid #ddd; border-radius: 4px; font-family: 'JetBrains Mono', monospace; font-size: 11px; background: #fff; color: #333; }
.manual-model-input:focus { outline: none; border-color: #000; }
.manual-model-input::placeholder { color: #aaa; font-style: italic; }
.pc-nudge { font-size: 12px; color: #a07000; background: #fffbf0; border: 1px solid #f5e6c0; border-radius: 4px; padding: 8px 10px; margin-top: 8px; line-height: 1.5; }
.selected-model-meta { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.meta-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 7px;
  border-radius: 3px;
  background: #f0f0f0;
  color: #666;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.3px;
  text-transform: uppercase;
}
.meta-chip.live { background: #f0f8f0; color: #4a4; }
.meta-chip.speed { background: #f0f0f0; color: #666; }

.power-toggle {
  position: relative;
  width: 38px;
  height: 22px;
  display: inline-flex;
  align-items: center;
}
.power-toggle input {
  position: absolute;
  inset: 0;
  opacity: 0;
  cursor: pointer;
}
.power-slider {
  width: 38px;
  height: 22px;
  border-radius: 999px;
  background: #ccc;
  position: relative;
  transition: background 0.2s ease;
}
.power-slider::after {
  content: '';
  position: absolute;
  top: 3px;
  left: 3px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: white;
  box-shadow: 0 1px 3px rgba(0,0,0,0.16);
  transition: transform 0.2s ease;
}
.power-toggle input:checked + .power-slider { background: #4a4; }
.power-toggle input:checked + .power-slider::after { transform: translateX(16px); }

.pc-toggle-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.toggle-label { font-size: 12px; color: #555; cursor: pointer; display: flex; align-items: center; gap: 5px; }
.toggle-hint { font-size: 11px; color: #aaa; }

.pc-actions { margin-top: 10px; }
.connect-btn { font-size: 12px; font-weight: 600; padding: 6px 18px; border: 1px solid #000; background: #000; color: #fff; border-radius: 4px; cursor: pointer; }
.connect-btn:hover { background: #222; }
.connect-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.disconnect-btn { font-size: 12px; font-weight: 600; padding: 6px 18px; border: 1px solid #ddd; background: #fff; color: #999; border-radius: 4px; cursor: pointer; }
.disconnect-btn:hover { border-color: #c44; color: #c44; }

.pc-login-progress { margin-top: 10px; }
.pc-waiting { font-size: 12px; color: #888; display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.waiting-dot { width: 8px; height: 8px; border-radius: 50%; background: #e8a500; animation: pulse-dot 1.4s ease-in-out infinite; }
@keyframes pulse-dot { 0%, 100% { opacity: 0.3; } 50% { opacity: 1; } }
.pc-paste { margin-top: 8px; }
.paste-label { font-size: 11px; color: #666; display: block; margin-bottom: 6px; }
.paste-row { display: flex; gap: 6px; }
.paste-input { flex: 1; padding: 6px 10px; border: 1px solid #ddd; border-radius: 4px; font-family: 'JetBrains Mono', monospace; font-size: 12px; }
.paste-input:focus { outline: none; border-color: #000; }
.paste-btn { font-size: 12px; font-weight: 600; padding: 6px 14px; border: 1px solid #000; background: #000; color: #fff; border-radius: 4px; cursor: pointer; }
.paste-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.pc-error { font-size: 12px; color: #c44; margin-top: 8px; }

/* Active models table */
.empty-state { font-size: 13px; color: #999; padding: 24px; text-align: center; border: 1px dashed #ddd; border-radius: 6px; }
.model-table-wrap { border: 1px solid #e0e0e0; border-radius: 6px; overflow-x: auto; }
.model-table { width: 100%; min-width: 1120px; border-collapse: collapse; font-size: 13px; }
.model-table th { text-align: left; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; color: #888; padding: 10px 12px; border-bottom: 1px solid #e0e0e0; background: #fafafa; white-space: nowrap; }
.model-table th.num { text-align: right; }
.model-table td { padding: 8px 12px; border-bottom: 1px solid #f4f4f4; color: #333; }
.model-table td.num { text-align: right; }
.model-id-cell { max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.score-band-col { min-width: 310px; }

.type-chip { font-size: 10px; font-weight: 600; padding: 2px 7px; border-radius: 3px; text-transform: uppercase; }
.type-chip.local { background: #e8f5e9; color: #2a7a2a; }
.type-chip.api { background: #fff3e0; color: #e65100; }
.type-chip.subscription { background: #f3e5f5; color: #7b1fa2; }

.cap-chips { display: flex; gap: 3px; flex-wrap: wrap; }
.cap-chip { font-size: 9px; font-weight: 600; padding: 1px 5px; border-radius: 2px; text-transform: uppercase; }
.cap-chip.json { background: #e8f5e9; color: #2e7d32; }
.cap-chip.scoring { background: #e3f2fd; color: #1565c0; }
.cap-chip.complete { background: #f3e5f5; color: #7b1fa2; }
.cap-hint { font-size: 10px; color: #ccc; font-style: italic; }

.bench-score { font-family: 'JetBrains Mono', monospace; font-weight: 700; padding: 1px 6px; border-radius: 3px; }
.bench-score.high { background: #e8f5e9; color: #2a7a2a; }
.bench-score.mid { background: #fff8e1; color: #a07000; }
.bench-score.low { background: #ffebee; color: #900; }
.bench-na { color: #ddd; }

.class-chip { font-size: 10px; font-weight: 700; padding: 2px 7px; border-radius: 3px; text-transform: uppercase; }
.class-chip.excellent { background: #e8f5e9; color: #2a7a2a; }
.class-chip.good { background: #e3f2fd; color: #1565c0; }
.class-chip.usable { background: #fff8e1; color: #a07000; }
.class-chip.limited { background: #fff3e0; color: #e65100; }
.class-chip.unsuitable { background: #ffebee; color: #900; }
.class-chip.error { background: #f5f5f5; color: #999; }

.bench-details { margin-top: 20px; }
.bench-toggle { font-size: 13px; font-weight: 600; cursor: pointer; color: #555; user-select: none; }
.bench-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 10px; margin-top: 12px; }
.bench-card { border: 1px solid #e8e8e8; border-radius: 6px; padding: 12px 14px; }
.bench-card-top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.bench-card-id { font-size: 11px; }
.bench-stats { display: flex; gap: 14px; flex-wrap: wrap; }
.bench-stat { display: flex; flex-direction: column; }
.bsv { font-size: 14px; font-weight: 700; font-family: 'JetBrains Mono', monospace; }
.bsl { font-size: 10px; color: #888; text-transform: uppercase; }
.bench-error { font-size: 11px; color: #c44; margin-top: 6px; }

.score-band-cell { min-width: 310px; }
.score-band { display: grid; grid-template-columns: repeat(5, minmax(52px, 1fr)); gap: 6px; }
.score-band-item {
  border-radius: 4px;
  padding: 6px 7px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  background: #f5f5f5;
}
.score-band-item.high { background: #e8f5e9; color: #2a7a2a; }
.score-band-item.mid { background: #fff8e1; color: #a07000; }
.score-band-item.low { background: #ffebee; color: #900; }
.score-band-label { font-size: 9px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; }
.score-band-value { font-family: 'JetBrains Mono', monospace; font-size: 12px; font-weight: 700; }

.suggested-roles { font-size: 11px; color: #555; }

/* Tag colors */
.cap-chip.json { background: #e8f5e9; color: #2e7d32; }
.cap-chip.depth { background: #e3f2fd; color: #1565c0; }
.cap-chip.reason { background: #fff3e0; color: #e65100; }
.cap-chip.context { background: #f3e5f5; color: #7b1fa2; }
.cap-chip.instruct { background: #e0f2f1; color: #00695c; }
.cap-chip.default { background: #f5f5f5; color: #666; }

/* Dimension bars */
.dim-bars { display: flex; flex-direction: column; gap: 4px; margin: 8px 0; }
.dim-bar-row { display: flex; align-items: center; gap: 6px; }
.dim-bar-label { font-size: 10px; color: #888; width: 55px; text-align: right; flex-shrink: 0; }
.dim-bar-track { flex: 1; height: 8px; background: #f0f0f0; border-radius: 4px; overflow: hidden; }
.dim-bar-fill { height: 100%; border-radius: 4px; transition: width 0.3s; }
.dim-bar-fill.high { background: #4a4; }
.dim-bar-fill.mid { background: #e8a500; }
.dim-bar-fill.low { background: #c44; }
.dim-bar-val { font-size: 10px; font-family: 'JetBrains Mono', monospace; color: #666; width: 35px; }

.bench-throughput { font-size: 11px; color: #888; margin-top: 6px; font-family: 'JetBrains Mono', monospace; }
.bench-tags { display: flex; gap: 4px; margin-top: 6px; flex-wrap: wrap; }

@media (max-width: 920px) {
  .settings-header,
  .section-header-row {
    flex-direction: column;
    align-items: flex-start;
  }

  .score-band {
    grid-template-columns: repeat(3, minmax(52px, 1fr));
  }
}
</style>
