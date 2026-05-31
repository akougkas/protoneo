<template>
  <div class="settings-page">
    <header class="settings-header">
      <div class="header-left">
        <router-link to="/" class="logo">PROTONEO</router-link>
        <span class="brand-divider"></span>
        <span class="product-tag">Settings</span>
      </div>
      <router-link to="/" class="back-link">&larr; Back</router-link>
    </header>

    <!-- No quality banner here - that belongs in Home, not Settings -->

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
            <div class="card-top">
              <div class="card-name-row">
                <span :class="['status-dot', node.online ? 'on' : 'off']"></span>
                <span class="card-name">{{ node.display_name || node.id }}</span>
                <span class="card-type-badge">{{ node.type === 'ollama' ? 'Ollama' : 'OpenAI-compat' }}</span>
              </div>
              <div class="card-controls">
                <label class="power-toggle">
                  <input type="checkbox" :checked="isProviderEnabled(node.id)" @change="toggleEndpoint(node.id, $event.target.checked)" />
                  <span class="power-slider"></span>
                </label>
                <span :class="['card-status-label', node.online && isProviderEnabled(node.id) ? 'connected' : '']">
                  {{ !isProviderEnabled(node.id) ? 'Off' : node.online ? (node.models?.length || 0) + ' discovered' : 'Offline' }}
                </span>
              </div>
            </div>
            <div class="card-detail mono">{{ node.url }}</div>

            <!-- Loaded vs available distinction -->
            <div v-if="node.online && node.loaded_model" class="card-loaded">
              In VRAM: <strong>{{ node.loaded_model }}</strong>
            </div>
            <div v-if="node.online && node.loaded_models?.length" class="card-loaded">
              In memory: <strong>{{ node.loaded_models.join(', ') }}</strong>
            </div>

            <!-- Active model selection -->
            <div v-if="node.online && node.models?.length" class="card-select">
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
              <span v-if="selectedModelMeta(node.id).registry?.supports_reasoning" class="meta-chip warn">reasoning</span>
              <span v-if="selectedModelMeta(node.id).registry?.supports_vision" class="meta-chip vision">vision</span>
              <span v-if="selectedModelMeta(node.id).registry?.structured_output !== 'unknown'" class="meta-chip">{{ selectedModelMeta(node.id).registry.structured_output }} json</span>
              <span v-if="selectedModelMeta(node.id).benchmark" :class="['class-chip', selectedModelMeta(node.id).benchmark.protoneo_class]">
                {{ selectedModelMeta(node.id).benchmark.protoneo_class }}
              </span>
            </div>
            <div v-if="selectedModelMeta(node.id)?.loaded === false" class="card-nudge">Selected model is not loaded in VRAM. Load it in the service, then refresh discovery.</div>

            <!-- Nudge when no models -->
            <div v-if="node.online && !node.models?.length && node.nudge" class="card-nudge">{{ node.nudge }}</div>
            <div v-if="!node.online" class="card-nudge">
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
            <div class="card-top">
              <div class="card-name-row">
                <span :class="['status-dot', node.online ? 'on' : 'off']"></span>
                <span class="card-name">{{ node.display_name || node.id }}</span>
              </div>
              <div class="card-controls">
                <label class="power-toggle">
                  <input type="checkbox" :checked="isProviderEnabled(node.id)" @change="toggleEndpoint(node.id, $event.target.checked)" />
                  <span class="power-slider"></span>
                </label>
                <span :class="['card-status-label', node.online && isProviderEnabled(node.id) ? 'connected' : '']">
                  {{ !isProviderEnabled(node.id) ? 'Off' : node.online ? (node.models?.length || 0) + ' discovered' : 'Offline' }}
                </span>
              </div>
            </div>
            <div class="card-detail mono">{{ node.url }}</div>
            <div v-if="node.online && node.loaded_model" class="card-loaded">In VRAM: <strong>{{ node.loaded_model }}</strong></div>
            <div v-if="node.online && node.models?.length" class="card-select">
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
              <span v-if="selectedModelMeta(node.id).registry?.supports_reasoning" class="meta-chip warn">reasoning</span>
              <span v-if="selectedModelMeta(node.id).registry?.supports_vision" class="meta-chip vision">vision</span>
              <span v-if="selectedModelMeta(node.id).registry?.structured_output !== 'unknown'" class="meta-chip">{{ selectedModelMeta(node.id).registry.structured_output }} json</span>
              <span v-if="selectedModelMeta(node.id).benchmark" :class="['class-chip', selectedModelMeta(node.id).benchmark.protoneo_class]">
                {{ selectedModelMeta(node.id).benchmark.protoneo_class }}
              </span>
            </div>
            <div v-if="selectedModelMeta(node.id)?.loaded === false" class="card-nudge">Selected model is not loaded in VRAM. Load it on this endpoint, then refresh discovery.</div>
            <div v-if="node.nudge" class="card-nudge">{{ node.nudge }}</div>
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
            <div class="card-top">
              <div class="card-name-row">
                <span :class="['status-dot', openrouterAvailable ? 'on' : 'off']"></span>
                <span class="card-name">OpenRouter</span>
              </div>
              <div class="card-controls">
                <label class="power-toggle">
                  <input type="checkbox" :checked="isProviderEnabled('openrouter')" @change="toggleProvider('openrouter', $event.target.checked)" />
                  <span class="power-slider"></span>
                </label>
                <span :class="['card-status-label', openrouterAvailable && isProviderEnabled('openrouter') ? 'connected' : '']">
                  {{ !isProviderEnabled('openrouter') ? 'Off' : openrouterAvailable ? openrouterModelCount + ' models' : 'Not configured' }}
                </span>
              </div>
            </div>
            <div class="card-toggle-row">
              <label class="toggle-label">
                <input type="checkbox" v-model="settings.openrouter_free_only" @change="saveAndRefresh" />
                Free tier only
              </label>
              <span class="toggle-hint" v-if="discovery.openrouter?.total_available">
                ({{ discovery.openrouter.total_available }} total)
              </span>
            </div>
            <div v-if="openrouterAvailable" class="card-select">
              <label class="select-label">Active model:</label>
              <select v-if="providerModelOptions('openrouter').length" class="provider-model-select" :disabled="!isProviderEnabled('openrouter')" :value="settings.active_models['openrouter'] || ''" @change="setActiveModel('openrouter', $event.target.value)">
                <option value="">Select a model...</option>
                <option v-for="m in providerModelOptions('openrouter')" :key="m.id" :value="m.id">{{ m.name || m.id }}</option>
              </select>
              <div class="manual-model-row">
                <input
                  class="manual-model-input"
                  placeholder="Custom OpenRouter model ID, e.g. openai/gpt-5.5"
                  :value="settings.active_models['openrouter'] || ''"
                  @change="setActiveModel('openrouter', $event.target.value.trim())"
                  @keydown.enter="setActiveModel('openrouter', $event.target.value.trim())"
                  :disabled="!isProviderEnabled('openrouter')"
                />
              </div>
            </div>
            <div v-if="selectedModelMeta('openrouter')" class="selected-model-meta">
              <span v-if="selectedModelMeta('openrouter').context_length" class="meta-chip">{{ formatContext(selectedModelMeta('openrouter').context_length) }} ctx</span>
              <span v-if="selectedModelMeta('openrouter').speed" class="meta-chip speed">{{ selectedModelMeta('openrouter').speed }} t/s</span>
              <span v-if="selectedModelMeta('openrouter').registry?.supports_reasoning" class="meta-chip warn">reasoning</span>
              <span v-if="selectedModelMeta('openrouter').registry?.supports_tools" class="meta-chip">tools</span>
              <span v-if="selectedModelMeta('openrouter').benchmark" :class="['class-chip', selectedModelMeta('openrouter').benchmark.protoneo_class]">
                {{ selectedModelMeta('openrouter').benchmark.protoneo_class }}
              </span>
            </div>
            <div v-if="!openrouterAvailable" class="card-nudge">Set OPENROUTER_API_KEY in .env to access cloud models.</div>
          </div>
        </div>
      </div>

      <!-- ─── Subscriptions ─── -->
      <div class="tier-group">
        <div class="tier-label-row">
          <span class="tier-dot subscription"></span>
          <h3 class="tier-name">Subscriptions</h3>
          <span class="tier-hint">OAuth login to ChatGPT, Gemini</span>
        </div>
        <div class="provider-grid">
          <div v-for="p in subscriptionProviders" :key="p.provider" :class="['provider-card', { connected: p.logged_in || p.has_credentials, disabled: !isProviderEnabled(p.provider) }]">
            <div class="card-top">
              <div class="card-name-row">
                <span :class="['status-dot', p.logged_in || p.has_credentials ? 'on' : 'off']"></span>
                <span class="card-name">{{ p.display_name }}</span>
                <span v-if="p.oauth_experimental" class="card-type-badge experimental">Experimental</span>
              </div>
              <div class="card-controls">
                <label class="power-toggle">
                  <input type="checkbox" :checked="isProviderEnabled(p.provider)" @change="toggleProvider(p.provider, $event.target.checked)" />
                  <span class="power-slider"></span>
                </label>
                <span :class="['card-status-label', (p.logged_in || p.has_credentials) && isProviderEnabled(p.provider) ? 'connected' : '']">
                  {{ !isProviderEnabled(p.provider) ? 'Off' : p.logged_in ? (p.token_type === 'oauth' ? 'OAuth' : 'Connected') : p.has_credentials ? 'API Key' : 'Not connected' }}
                </span>
              </div>
            </div>

            <div v-if="p.email" class="card-detail">{{ p.email }}</div>
            <div v-if="p.logged_in && !p.expired" class="card-detail">Token expires {{ formatExpiry(p.expires_at) }}</div>

            <!-- Active model from discovered, with manual override for new subscription models -->
            <div v-if="(p.logged_in || p.has_credentials) && isProviderEnabled(p.provider)" class="card-select">
              <label class="select-label">Active model:</label>
              <select v-if="providerModelOptions(p.provider).length" class="provider-model-select" :disabled="!isProviderEnabled(p.provider)" :value="settings.active_models[p.provider] || ''" @change="setActiveModel(p.provider, $event.target.value)">
                <option value="">Select a model...</option>
                <option v-for="m in providerModelOptions(p.provider)" :key="m.id" :value="m.id">{{ m.name || m.id }}</option>
              </select>
              <div class="manual-model-row">
                <input
                  class="manual-model-input"
                  :placeholder="'Custom model ID, e.g. gpt-5.5'"
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
              <span v-if="selectedModelMeta(p.provider).registry?.supports_reasoning" class="meta-chip warn">reasoning</span>
              <span v-if="selectedModelMeta(p.provider).registry?.supports_tools" class="meta-chip">tools</span>
              <span v-if="selectedModelMeta(p.provider).benchmark" :class="['class-chip', selectedModelMeta(p.provider).benchmark.protoneo_class]">
                {{ selectedModelMeta(p.provider).benchmark.protoneo_class }}
              </span>
            </div>

            <!-- Nudge from discovery -->
            <div v-if="discoveryNudge(p.provider)" class="card-nudge">{{ discoveryNudge(p.provider) }}</div>
            <div v-else-if="p.connection_hint" class="card-nudge">{{ p.connection_hint }}</div>

            <!-- Login flow -->
            <div v-if="pendingLogin === p.provider" class="card-login-progress">
              <div class="card-waiting"><span class="waiting-dot"></span> Waiting for browser login...</div>
              <div v-if="showManualPaste" class="card-paste">
                <label class="paste-label">Paste redirect URL:</label>
                <div class="paste-row">
                  <input v-model="pasteCode" class="paste-input" @keydown.enter="submitPasteCode(p.provider)" />
                  <button class="paste-btn" @click="submitPasteCode(p.provider)" :disabled="!pasteCode">Submit</button>
                </div>
              </div>
            </div>

            <div class="card-actions">
              <button v-if="p.oauth_enabled !== false && !p.logged_in && !p.has_credentials" class="connect-btn" :disabled="pendingLogin === p.provider" @click="startLogin(p.provider)">
                {{ pendingLogin === p.provider ? 'Waiting...' : 'Connect' }}
              </button>
              <button v-if="p.logged_in" class="disconnect-btn" @click="disconnect(p.provider)">Disconnect</button>
            </div>
            <div v-if="loginError && pendingLogin === p.provider" class="card-error">{{ loginError }}</div>
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

    <!-- ═══════════════ VLM FIGURE DESCRIPTION ═══════════════ -->
    <section class="section">
      <div class="section-header-row">
        <div>
          <h2 class="section-title">VLM Figure Description</h2>
          <p class="section-desc">Optional figure descriptions during PDF parsing. Keep disabled for fastest first-pass reviews.</p>
        </div>
        <button class="action-btn-sm" @click="testVlm" :disabled="testingVlm || !settings.vlm_endpoint.url">
          {{ testingVlm ? 'Testing...' : 'Test Connection' }}
        </button>
      </div>

      <div class="vlm-form">
        <label class="toggle-label vlm-enable">
          <input type="checkbox" v-model="settings.vlm_endpoint.enabled" @change="saveSettings" />
          Enable VLM figure descriptions during parsing
        </label>
        <div class="vlm-row">
          <label class="vlm-label">Endpoint URL</label>
          <input
            class="vlm-input"
            type="text"
            placeholder="http://192.168.86.141:8081/v1/chat/completions"
            v-model="settings.vlm_endpoint.url"
            @change="saveSettings"
          />
        </div>
        <div class="vlm-row">
          <label class="vlm-label">Model name</label>
          <input
            class="vlm-input"
            type="text"
            placeholder="qwen3-vl-30b"
            v-model="settings.vlm_endpoint.model"
            @change="saveSettings"
          />
        </div>
        <div class="vlm-row vlm-row--wide">
          <label class="vlm-label">Description prompt</label>
          <textarea
            class="vlm-textarea"
            rows="3"
            v-model="settings.vlm_endpoint.prompt"
            @change="saveSettings"
          ></textarea>
        </div>
        <div class="vlm-row-group">
          <div class="vlm-row vlm-row--narrow">
            <label class="vlm-label">Temperature</label>
            <input class="vlm-input" type="number" step="0.1" min="0" max="2" v-model.number="settings.vlm_endpoint.temperature" @change="saveSettings" />
          </div>
          <div class="vlm-row vlm-row--narrow">
            <label class="vlm-label">Top-P</label>
            <input class="vlm-input" type="number" step="0.1" min="0" max="1" v-model.number="settings.vlm_endpoint.top_p" @change="saveSettings" />
          </div>
          <div class="vlm-row vlm-row--narrow">
            <label class="vlm-label">Timeout (s)</label>
            <input class="vlm-input" type="number" step="10" min="30" max="600" v-model.number="settings.vlm_endpoint.timeout" @change="saveSettings" />
          </div>
          <div class="vlm-row vlm-row--narrow">
            <label class="vlm-label">Concurrency</label>
            <input class="vlm-input" type="number" step="1" min="1" max="4" v-model.number="settings.vlm_endpoint.concurrency" @change="saveSettings" />
          </div>
        </div>
        <div v-if="vlmTestResult" :class="['vlm-test-result', vlmTestResult.ok ? 'ok' : 'err']">
          {{ vlmTestResult.message }}
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import {
  getProviders, getSettings, updateSettings, discoverModels, getModels,
  startBenchmark, getBenchmarkResults,
  beginProviderLogin, getLoginStatus, completeProviderLogin, providerLogout,
} from '../api/kernel.js'

const providers = ref([])
const discovery = ref({})
const registeredModels = ref([])
const settings = reactive({
  localhost_endpoints: [],
  lan_endpoints: [],
  openrouter_free_only: true,
  provider_enabled: {},
  active_models: {},
  vlm_endpoint: { enabled: false, url: '', model: '', prompt: '', temperature: 0.1, top_p: 0.9, timeout: 120, concurrency: 1 },
  benchmark_results: [],
  discovered_models: {},
})

const benchmarkResults = ref([])
const showBenchDetails = ref(false)
const discovering = ref(false)
const benchmarking = ref(false)
const benchProgress = ref('')

const testingVlm = ref(false)
const vlmTestResult = ref(null)
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
  providers.value.filter(p => ['openai'].includes(p.provider))  // anthropic removed
)
const openrouterAvailable = computed(() => providers.value.find(p => p.provider === 'openrouter')?.has_credentials ?? false)
const openrouterModelCount = computed(() => discovery.value.openrouter?.models?.length || 0)

function modelListFromCache(providerName, sourceSettings = settings) {
  const cached = sourceSettings.discovered_models?.[providerName]
  return Array.isArray(cached) ? cached : []
}

function endpointDiscoveryGroup(endpoints = [], sourceSettings = settings) {
  return endpoints.map(ep => {
    const models = modelListFromCache(ep.id, sourceSettings)
    const selected = sourceSettings.active_models?.[ep.id] || ''
    const loadedModel = models.find(m => m.loaded)?.id || selected || null
    return {
      id: ep.id,
      name: ep.id,
      display_name: ep.display_name || ep.id,
      location: ep.location || 'lan',
      url: ep.url,
      type: ep.type || 'openai',
      online: models.length > 0,
      models,
      loaded_model: loadedModel,
      loaded_models: models.filter(m => m.loaded).map(m => m.id),
      nudge: '',
    }
  })
}

function discoveryFromSettings(sourceSettings = settings) {
  const cached = sourceSettings.discovered_models || {}
  const out = {}
  out.localhost = endpointDiscoveryGroup(sourceSettings.localhost_endpoints || [], sourceSettings)
  out.lan = endpointDiscoveryGroup(sourceSettings.lan_endpoints || [], sourceSettings)

  const endpointIds = new Set([...out.localhost, ...out.lan].map(n => n.id))
  for (const [providerName, models] of Object.entries(cached)) {
    if (endpointIds.has(providerName) || !Array.isArray(models)) continue
    out[providerName] = {
      provider: providerName,
      online: models.length > 0,
      models,
    }
  }
  return out
}

function mergeDiscovery(base, live) {
  const merged = { ...base, ...live }
  for (const group of ['localhost', 'lan']) {
    if (Array.isArray(base[group]) && Array.isArray(live[group])) {
      const byId = Object.fromEntries(base[group].map(node => [node.id, node]))
      for (const node of live[group]) byId[node.id] = { ...(byId[node.id] || {}), ...node }
      merged[group] = Object.values(byId)
    }
  }
  return merged
}

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
  for (const prov of ['openai']) {  // anthropic removed
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

const registryIndex = computed(() => {
  const index = {}
  for (const model of registeredModels.value) {
    index[model.model_id] = model
  }
  return index
})

const activeModelList = computed(() => {
  const list = []
  for (const [provider, modelId] of Object.entries(settings.active_models)) {
    if (!modelId || !isProviderEnabled(provider)) continue
    const model = findDiscoveredModel(provider, modelId)
    const registry = registryIndex.value[`${provider}/${modelId}`] || null
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
      registry,
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

function providerModelOptions(providerName) {
  const models = [...discoveredProviderModels(providerName)]
  const selected = settings.active_models[providerName]
  if (selected && !models.some(m => m.id === selected)) {
    models.unshift({
      id: selected,
      name: `${selected} (custom)`,
      source: providerName,
      provider_type: providerName === 'openrouter' ? 'api' : 'subscription',
    })
  }
  return models
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
  const registry = registryIndex.value[`${provider}/${modelId}`] || null
  return {
    model,
    registry,
    context_length: model?.context_length || registry?.max_context || null,
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
    const [pRes, sRes, bRes, mRes] = await Promise.all([getProviders(), getSettings(), getBenchmarkResults(), getModels()])
    providers.value = pRes.data.providers || []
    const loaded = sRes.data
    if (!loaded.vlm_endpoint) loaded.vlm_endpoint = { enabled: false, url: '', model: '', prompt: '', temperature: 0.1, top_p: 0.9, timeout: 120, concurrency: 1 }
    if (loaded.vlm_endpoint.enabled === undefined) loaded.vlm_endpoint.enabled = false
    if (!loaded.vlm_endpoint.timeout) loaded.vlm_endpoint.timeout = 120
    if (!loaded.vlm_endpoint.concurrency) loaded.vlm_endpoint.concurrency = 1
    Object.assign(settings, loaded)
    discovery.value = discoveryFromSettings(loaded)
    benchmarkResults.value = bRes.data.results || []
    registeredModels.value = mRes.data.models || []
  } catch (e) { console.error('Load failed:', e) }
}

async function refreshDiscovery() {
  discovering.value = true
  try {
    const live = (await discoverModels()).data || {}
    const sRes = await getSettings()
    const loaded = sRes.data || {}
    if (!loaded.vlm_endpoint) loaded.vlm_endpoint = settings.vlm_endpoint
    Object.assign(settings, loaded)
    discovery.value = mergeDiscovery(discoveryFromSettings(loaded), live)
  }
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
      vlm_endpoint: settings.vlm_endpoint,
    })
  }
  catch (e) { console.error('Save failed:', e) }
}

async function saveAndRefresh() { await saveSettings(); await refreshDiscovery() }

async function testVlm() {
  if (!settings.vlm_endpoint.url) return
  testingVlm.value = true
  vlmTestResult.value = null
  try {
    const base = settings.vlm_endpoint.url.replace(/\/chat\/completions$/, '')
    const res = await fetch(base + '/models')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    const models = data.data || data.models || []
    const names = models.map(m => m.id || m.name).join(', ')
    vlmTestResult.value = { ok: true, message: `Connected. Models: ${names}` }
  } catch (e) {
    vlmTestResult.value = { ok: false, message: `Connection failed: ${e.message}` }
  } finally {
    testingVlm.value = false
  }
}

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
  padding: var(--pn-space-7) var(--pn-space-5) var(--pn-space-8);
}
.settings-header {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: var(--pn-space-6);
  padding-bottom: var(--pn-space-5);
  border-bottom: 1px solid var(--pn-border);
}
.header-left { display: flex; align-items: center; gap: var(--pn-space-3); }
.brand-divider { width: 1px; height: 18px; background: var(--pn-border-strong); }
.logo {
  font-family: var(--pn-mono); font-size: 13px; font-weight: 700;
  letter-spacing: 0.18em; text-decoration: none; color: var(--pn-text);
}
.logo:hover { color: var(--pn-accent); }
.product-tag {
  font-family: var(--pn-serif); font-size: 14px; font-weight: 500;
  font-style: italic; color: var(--pn-text-secondary);
}
.back-link {
  font-size: 11px; color: var(--pn-text-muted); text-decoration: none;
  padding: var(--pn-space-2) var(--pn-space-3); border: 1px solid var(--pn-border);
  letter-spacing: 0.04em; transition: all var(--pn-duration) var(--pn-ease);
}
.back-link:hover { border-color: var(--pn-text); color: var(--pn-text); }

.section { margin-bottom: var(--pn-space-7); }
.section-header-row {
  display: flex; justify-content: space-between; align-items: flex-start;
  margin-bottom: var(--pn-space-5); gap: var(--pn-space-4);
}
.section-title {
  font-family: var(--pn-mono); font-size: 11px; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.08em;
  color: var(--pn-text-secondary); margin-bottom: var(--pn-space-1);
}
.section-desc { font-size: 12px; color: var(--pn-text-muted); }
.action-btn-sm {
  font-size: 11px; font-weight: 600; padding: var(--pn-space-2) var(--pn-space-4);
  border: 1px solid var(--pn-border); background: var(--pn-surface);
  color: var(--pn-text); cursor: pointer; letter-spacing: 0.04em;
  transition: all var(--pn-duration) var(--pn-ease);
}
.action-btn-sm:hover { border-color: var(--pn-text); }
.action-btn-sm:disabled { opacity: 0.35; cursor: not-allowed; }
.bench-btn { border-color: var(--pn-text); background: var(--pn-text); color: var(--pn-bg); }
.bench-btn:hover { background: #222; }

.tier-group { margin-bottom: var(--pn-space-5); }
.tier-label-row {
  display: flex; align-items: center; gap: var(--pn-space-2);
  margin-bottom: var(--pn-space-3); padding-bottom: var(--pn-space-2);
  border-bottom: 1px solid var(--pn-border);
}
.tier-dot { width: 8px; height: 8px; border-radius: 50%; }
.tier-dot.local { background: var(--pn-ok); }
.tier-dot.homelab { background: var(--pn-text-muted); }
.tier-dot.api { background: var(--pn-warn); }
.tier-dot.subscription { background: var(--pn-text-muted); }
.tier-name {
  font-family: var(--pn-mono); font-size: 10px; font-weight: 700;
  color: var(--pn-text-secondary); text-transform: uppercase; letter-spacing: 0.06em;
}
.tier-hint { font-size: 10px; color: var(--pn-text-ghost); }

.provider-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: var(--pn-space-3); }
.provider-card {
  border: 1px solid var(--pn-border);
  padding: var(--pn-space-4) var(--pn-space-4);
  background: var(--pn-surface);
  transition: all var(--pn-duration) var(--pn-ease);
}
.provider-card:hover { border-color: var(--pn-border-strong); }
.provider-card.connected { border-color: var(--pn-border); }
.provider-card.disabled { opacity: 0.5; }
.card-top { display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--pn-space-2); }
.card-controls { display: flex; align-items: center; gap: var(--pn-space-3); }
.card-name-row { display: flex; align-items: center; gap: var(--pn-space-2); }
.status-dot { width: 6px; height: 6px; border-radius: 50%; }
.status-dot.on { background: var(--pn-ok); }
.status-dot.off { border: 1.5px solid var(--pn-border-strong); width: 6px; height: 6px; }
.card-name { font-size: 13px; font-weight: 700; color: var(--pn-text); }
.card-type-badge {
  font-family: var(--pn-mono); font-size: 8px; font-weight: 600;
  padding: 1px 5px; background: var(--pn-border); color: var(--pn-text-muted);
  text-transform: uppercase; letter-spacing: 0.06em;
}
.card-type-badge.experimental { background: var(--pn-warn-dim); color: var(--pn-warn); }
.card-status-label {
  font-family: var(--pn-mono); font-size: 9px; font-weight: 600;
  color: var(--pn-text-muted); text-transform: uppercase; letter-spacing: 0.06em;
}
.card-status-label.connected { color: var(--pn-ok); }
.card-detail { font-size: 11px; color: var(--pn-text-muted); margin-bottom: 3px; }
.card-detail.mono, .mono { font-size: 10px; }
.card-loaded {
  font-size: 11px; color: var(--pn-text); margin: var(--pn-space-2) 0;
  padding: var(--pn-space-1) var(--pn-space-2);
  border-left: 2px solid var(--pn-ok); background: var(--pn-ok-dim);
}
.card-select { margin: var(--pn-space-2) 0; }
.select-label {
  font-family: var(--pn-mono); font-size: 9px; font-weight: 600;
  color: var(--pn-text-muted); display: block; margin-bottom: 3px;
  text-transform: uppercase; letter-spacing: 0.06em;
}
.provider-model-select { width: 100%; font-size: 11px; }
.provider-model-select:focus { outline: none; border-color: #000; }
.manual-model-row { display: flex; gap: 6px; margin-top: 6px; }
.manual-model-input { flex: 1; padding: 6px 8px; border: 1px solid #ddd; border-radius: 4px; font-family: 'JetBrains Mono', monospace; font-size: 11px; background: #fff; color: #333; }
.manual-model-input:focus { outline: none; border-color: #000; }
.manual-model-input::placeholder { color: #aaa; font-style: italic; }
.card-nudge {
  font-size: 11px; color: var(--pn-warn); background: var(--pn-warn-dim);
  border: 1px solid var(--pn-warn); padding: var(--pn-space-2) var(--pn-space-3);
  margin-top: var(--pn-space-2); line-height: 1.5;
}
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
.meta-chip.warn { background: #fff3e0; color: #a06000; }
.meta-chip.vision { background: #e3f2fd; color: #1565c0; }

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
.power-toggle input:checked + .power-slider { background: var(--pn-accent); }
.power-toggle input:checked + .power-slider::after { transform: translateX(16px); }

.card-toggle-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.toggle-label { font-size: 12px; color: #555; cursor: pointer; display: flex; align-items: center; gap: 5px; }
.toggle-hint { font-size: 11px; color: #aaa; }

.card-actions { margin-top: 10px; }
.connect-btn { font-size: 12px; font-weight: 600; padding: 6px 18px; border: 1px solid #000; background: #000; color: #fff; border-radius: 4px; cursor: pointer; }
.connect-btn:hover { background: #222; }
.connect-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.disconnect-btn { font-size: 12px; font-weight: 600; padding: 6px 18px; border: 1px solid #ddd; background: #fff; color: #999; border-radius: 4px; cursor: pointer; }
.disconnect-btn:hover { border-color: #c44; color: #c44; }

.card-login-progress { margin-top: 10px; }
.card-waiting { font-size: 12px; color: #888; display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.waiting-dot { width: 8px; height: 8px; border-radius: 50%; background: #e8a500; animation: pulse-dot 1.4s ease-in-out infinite; }
@keyframes pulse-dot { 0%, 100% { opacity: 0.3; } 50% { opacity: 1; } }
.card-paste { margin-top: 8px; }
.paste-label { font-size: 11px; color: #666; display: block; margin-bottom: 6px; }
.paste-row { display: flex; gap: 6px; }
.paste-input { flex: 1; padding: 6px 10px; border: 1px solid #ddd; border-radius: 4px; font-family: 'JetBrains Mono', monospace; font-size: 12px; }
.paste-input:focus { outline: none; border-color: #000; }
.paste-btn { font-size: 12px; font-weight: 600; padding: 6px 14px; border: 1px solid #000; background: #000; color: #fff; border-radius: 4px; cursor: pointer; }
.paste-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.card-error { font-size: 11px; color: var(--pn-err); margin-top: var(--pn-space-2); }

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

/* VLM Section */
.vlm-form { display: flex; flex-direction: column; gap: var(--pn-space-3); }
.vlm-enable { margin-bottom: var(--pn-space-1); }
.vlm-row { display: flex; flex-direction: column; gap: var(--pn-space-1); }
.vlm-row--wide { max-width: 100%; }
.vlm-row--narrow { max-width: 160px; }
.vlm-row-group { display: flex; gap: var(--pn-space-4); flex-wrap: wrap; }
.vlm-label { font-size: 11px; font-weight: 600; color: var(--pn-text-secondary); text-transform: uppercase; letter-spacing: 0.04em; }
.vlm-input {
  font-family: var(--pn-mono); font-size: 12px; padding: var(--pn-space-2) var(--pn-space-3);
  border: 1px solid var(--pn-border); background: var(--pn-surface); color: var(--pn-text);
  transition: border-color var(--pn-duration) var(--pn-ease);
}
.vlm-input:focus { border-color: var(--pn-text); outline: none; }
.vlm-textarea {
  font-family: var(--pn-mono); font-size: 12px; padding: var(--pn-space-2) var(--pn-space-3);
  border: 1px solid var(--pn-border); background: var(--pn-surface); color: var(--pn-text);
  resize: vertical; min-height: 60px;
}
.vlm-textarea:focus { border-color: var(--pn-text); outline: none; }
.vlm-test-result {
  font-size: 12px; padding: var(--pn-space-2) var(--pn-space-3);
  border: 1px solid var(--pn-border);
}
.vlm-test-result.ok { border-color: #2a7a2a; color: #2a7a2a; background: #e8f5e9; }
.vlm-test-result.err { border-color: #c44; color: #c44; background: #ffebee; }

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
