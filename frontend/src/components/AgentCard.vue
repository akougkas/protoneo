<template>
  <div :class="['agent-card', agent.status]">
    <div class="card-header">
      <span :class="['agent-dot', agent.status]"></span>
      <span class="agent-role">{{ agent.role }}</span>
    </div>
    <div class="agent-model" v-if="agent.model">{{ agent.model }}</div>
    <div class="agent-status-text">
      {{ statusLabel }}
      <span v-if="agent.duration" class="agent-timing">{{ agent.duration }}s</span>
      <span v-if="agent.tokens" class="agent-tokens">{{ agent.tokens }} tok</span>
    </div>
    <!-- Live streaming text preview -->
    <div v-if="streamingText && agent.status === 'running'" class="stream-preview">
      <div class="stream-text">{{ streamTail }}</div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  agent: { type: Object, required: true },
  streamingText: { type: String, default: '' },
})

const statusLabel = computed(() => {
  switch (props.agent.status) {
    case 'running': return 'Reviewing...'
    case 'done': return 'Complete'
    case 'error': return 'Failed'
    case 'waiting': return 'Waiting'
    default: return props.agent.status
  }
})

// Show last ~200 chars of streaming output for a live preview
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
  transition: border-color 0.2s;
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

.agent-model {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  color: #888;
  margin-bottom: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.agent-status-text {
  font-size: 12px;
  color: #666;
  display: flex;
  align-items: center;
  gap: 8px;
}

.agent-timing {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  color: #999;
}

.agent-tokens {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  color: #aaa;
}

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
}
</style>
