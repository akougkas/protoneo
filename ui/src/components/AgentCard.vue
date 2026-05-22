<template>
  <div :class="['agent-card', agent.status]">
    <div class="card-header">
      <span :class="['agent-dot', agent.status]"></span>
      <span class="agent-role">{{ agent.role }}</span>
      <span v-if="agent.status === 'running' && streamingText" class="stream-counter">
        {{ streamingText.length.toLocaleString() }} chars
      </span>
    </div>
    <div class="agent-model" v-if="agent.model">{{ agent.model }}</div>
    <div class="agent-metrics">
      <span class="metric-label">{{ statusLabel }}</span>
      <span v-if="agent.duration" class="metric mono">{{ agent.duration }}s</span>
      <span v-if="agent.tokens" class="metric mono">{{ agent.tokens.toLocaleString() }} tok</span>
      <span v-if="agent.completionTokens" class="metric mono dim">{{ agent.completionTokens.toLocaleString() }} out</span>
      <span v-if="throughput" class="metric mono dim">{{ throughput }} tok/s</span>
    </div>
    <!-- Live streaming text preview -->
    <div v-if="streamingText && agent.status === 'running'" class="stream-preview">
      <div :class="['stream-text', { expanded }]" ref="streamEl">{{ expanded ? streamingText : streamTail }}</div>
      <button v-if="streamingText.length > 200" class="expand-toggle" @click="expanded = !expanded">
        {{ expanded ? 'Collapse' : 'Expand' }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  agent: { type: Object, required: true },
  streamingText: { type: String, default: '' },
})

const expanded = ref(false)

const statusLabel = computed(() => {
  switch (props.agent.status) {
    case 'running': return 'Reviewing...'
    case 'done': return 'Complete'
    case 'error': return 'Failed'
    case 'waiting': return 'Waiting'
    default: return props.agent.status
  }
})

const throughput = computed(() => {
  if (props.agent.tokens && props.agent.duration && props.agent.duration > 0) {
    return (props.agent.tokens / props.agent.duration).toFixed(1)
  }
  return null
})

const streamTail = computed(() => {
  const text = props.streamingText
  if (!text) return ''
  if (text.length <= 200) return text
  return '...' + text.slice(-200)
})
</script>

<style scoped>
.agent-card {
  border: 1px solid var(--pn-border);
  padding: var(--pn-space-3) var(--pn-space-4);
  transition: all var(--pn-duration) var(--pn-ease);
  position: relative;
  background: var(--pn-surface);
}
.agent-card::before {
  content: '';
  position: absolute;
  left: 0; top: 0; bottom: 0;
  width: 2px;
  background: var(--pn-border);
  transition: background var(--pn-duration) var(--pn-ease);
}

.agent-card.running { border-color: var(--pn-warn); }
.agent-card.running::before { background: var(--pn-warn); }

.agent-card.done { border-color: var(--pn-ok); }
.agent-card.done::before { background: var(--pn-ok); }

.agent-card.error { border-color: var(--pn-err); }
.agent-card.error::before { background: var(--pn-err); }

.card-header {
  display: flex;
  align-items: center;
  gap: var(--pn-space-2);
  margin-bottom: var(--pn-space-1);
}

.agent-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}
.agent-dot.waiting { background: var(--pn-border-strong); }
.agent-dot.running { background: var(--pn-warn); animation: pn-pulse 1.8s ease-in-out infinite; }
.agent-dot.done { background: var(--pn-ok); }
.agent-dot.error { background: var(--pn-err); }

.agent-role {
  font-family: var(--pn-mono);
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.stream-counter {
  margin-left: auto;
  font-size: 9px;
  color: var(--pn-warn);
  animation: pn-pulse 1.8s ease-in-out infinite;
  letter-spacing: 0.02em;
}

.agent-model {
  font-size: 10px;
  color: var(--pn-text-muted);
  margin-bottom: var(--pn-space-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-metrics {
  font-size: 11px;
  color: var(--pn-text-secondary);
  display: flex;
  align-items: center;
  gap: var(--pn-space-2);
  flex-wrap: wrap;
}

.metric-label { font-weight: 500; }
.metric.mono { font-size: 10px; color: var(--pn-text-muted); }
.metric.dim { color: var(--pn-text-ghost); }

.stream-preview {
  margin-top: var(--pn-space-2);
  padding-top: var(--pn-space-2);
  border-top: 1px solid var(--pn-border);
}

.stream-text {
  font-size: 11px;
  line-height: 1.5;
  color: var(--pn-text-secondary);
  max-height: 80px;
  overflow: hidden;
  white-space: pre-wrap;
  word-break: break-word;
  transition: max-height var(--pn-duration) var(--pn-ease);
}
.stream-text.expanded {
  max-height: 400px;
  overflow-y: auto;
}

.expand-toggle {
  margin-top: var(--pn-space-1);
  font-size: 9px;
  font-weight: 600;
  color: var(--pn-text-muted);
  background: none;
  border: none;
  cursor: pointer;
  padding: 1px 4px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.expand-toggle:hover {
  color: var(--pn-text);
}
</style>
