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
  border: 1px solid #e0e0e0;
  border-radius: 5px;
  padding: 14px 16px;
  transition: border-color 0.2s, background 0.2s;
}

.agent-card.running {
  border-color: #e8a500;
  background: #fffdf5;
}

.agent-card.done {
  border-color: #4a4;
  background: #f8fcf8;
}

.agent-card.error {
  border-color: #c44;
  background: #fff5f5;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.agent-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.agent-dot.waiting { background: #ccc; }
.agent-dot.running {
  background: #e8a500;
  animation: pulse 1.5s infinite;
}
.agent-dot.done { background: #4a4; }
.agent-dot.error { background: #c44; }

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

.agent-role {
  font-size: 13px;
  font-weight: 600;
}

.stream-counter {
  margin-left: auto;
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  color: #e8a500;
  animation: pulse 1.5s infinite;
}

.agent-model {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  color: #888;
  margin-bottom: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-metrics {
  font-size: 12px;
  color: #666;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.metric-label { font-weight: 500; }

.metric.mono {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  color: #999;
}

.metric.dim { color: #bbb; }

.stream-preview {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid #f0e8c8;
}

.stream-text {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  line-height: 1.5;
  color: #555;
  max-height: 80px;
  overflow: hidden;
  white-space: pre-wrap;
  word-break: break-word;
  transition: max-height 0.2s;
}

.stream-text.expanded {
  max-height: 400px;
  overflow-y: auto;
}

.expand-toggle {
  margin-top: 4px;
  font-size: 10px;
  font-weight: 600;
  color: #888;
  background: none;
  border: none;
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 2px;
}

.expand-toggle:hover {
  color: #000;
  background: #f0f0f0;
}
</style>
