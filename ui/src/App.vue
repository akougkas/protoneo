<template>
  <router-view />
</template>

<script setup>
import { ref, provide, onMounted } from 'vue'
import { getManifests } from './api/kernel.js'

const manifests = ref({})
const activeApp = ref(null)

provide('manifests', manifests)
provide('activeApp', activeApp)

onMounted(async () => {
  try {
    const res = await getManifests()
    const list = res.data.apps || res.data.manifests || res.data || []
    const map = {}
    for (const m of (Array.isArray(list) ? list : [])) {
      map[m.name] = m
    }
    manifests.value = map
    const names = Object.keys(map)
    if (names.length > 0) {
      activeApp.value = map[names[0]]
    }
  } catch (e) {
    console.warn('Failed to load manifests:', e)
  }
})
</script>

<style>
/* ═══════════════════════════════════════════════════════════
   PROTONEO DESIGN SYSTEM
   Research-grade instrument aesthetic.
   IBM Plex Mono (code/data) + IBM Plex Serif (headings/prose).
   Sharp rectangles. Precise spacing. No decoration.
   ═══════════════════════════════════════════════════════════ */

:root {
  /* ── Palette ── */
  --pn-black: #0a0a0a;
  --pn-white: #fafaf8;
  --pn-bg: #fafaf8;
  --pn-surface: #ffffff;
  --pn-accent: #0d9488;
  --pn-accent-dim: #0d948822;
  --pn-accent-bright: #14b8a6;
  --pn-border: #e2e0dc;
  --pn-border-strong: #c8c6c1;
  --pn-text: #0a0a0a;
  --pn-text-secondary: #5c5a56;
  --pn-text-muted: #9c9a96;
  --pn-text-ghost: #c8c6c1;

  /* ── Status ── */
  --pn-ok: #16a34a;
  --pn-ok-dim: #16a34a18;
  --pn-warn: #ca8a04;
  --pn-warn-dim: #ca8a0418;
  --pn-err: #dc2626;
  --pn-err-dim: #dc262618;
  --pn-info: #2563eb;
  --pn-info-dim: #2563eb18;

  /* ── Typography ── */
  --pn-mono: 'IBM Plex Mono', 'JetBrains Mono', monospace;
  --pn-serif: 'IBM Plex Serif', 'Georgia', serif;
  --pn-sans: 'Noto Sans SC', system-ui, sans-serif;

  /* ── Spacing (8px base grid) ── */
  --pn-space-1: 4px;
  --pn-space-2: 8px;
  --pn-space-3: 12px;
  --pn-space-4: 16px;
  --pn-space-5: 24px;
  --pn-space-6: 32px;
  --pn-space-7: 48px;
  --pn-space-8: 64px;

  /* ── Transitions ── */
  --pn-ease: cubic-bezier(0.22, 1, 0.36, 1);
  --pn-duration: 0.2s;
}

/* ── Reset ── */
*, *::before, *::after {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

html {
  font-size: 16px;
  -webkit-text-size-adjust: 100%;
}

/* ── Root ── */
#app {
  font-family: var(--pn-mono);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  color: var(--pn-text);
  background: var(--pn-bg);
  min-height: 100vh;
  line-height: 1.5;
  font-size: 13px;
  letter-spacing: -0.01em;
}

/* ── Noise texture overlay ── */
#app::before {
  content: '';
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 9999;
  opacity: 0.025;
  background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
  background-repeat: repeat;
  background-size: 256px 256px;
}

/* ── Typography ── */
h1, h2, h3, h4, h5, h6 {
  font-family: var(--pn-serif);
  font-weight: 600;
  letter-spacing: -0.02em;
  line-height: 1.2;
}

/* ── Links ── */
a {
  color: var(--pn-accent);
  text-decoration: none;
  transition: color var(--pn-duration) var(--pn-ease);
}
a:hover {
  color: var(--pn-text);
}

/* ── Scrollbar ── */
::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}
::-webkit-scrollbar-track {
  background: transparent;
}
::-webkit-scrollbar-thumb {
  background: var(--pn-border-strong);
  border-radius: 0;
}
::-webkit-scrollbar-thumb:hover {
  background: var(--pn-text-muted);
}

/* ── Selection ── */
::selection {
  background: var(--pn-accent);
  color: white;
}

/* ── Buttons (global reset) ── */
button {
  font-family: var(--pn-mono);
  font-size: 12px;
  cursor: pointer;
  border: 1px solid var(--pn-border);
  background: var(--pn-surface);
  color: var(--pn-text);
  padding: var(--pn-space-2) var(--pn-space-4);
  transition: all var(--pn-duration) var(--pn-ease);
  letter-spacing: 0.02em;
}
button:hover:not(:disabled) {
  border-color: var(--pn-text);
}
button:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

/* ── Inputs ── */
input, select, textarea {
  font-family: var(--pn-mono);
  font-size: 12px;
  border: 1px solid var(--pn-border);
  background: var(--pn-surface);
  color: var(--pn-text);
  padding: var(--pn-space-2) var(--pn-space-3);
  transition: border-color var(--pn-duration) var(--pn-ease);
  outline: none;
}
input:focus, select:focus, textarea:focus {
  border-color: var(--pn-accent);
}

/* ── Utility: fade-in animation ── */
@keyframes pn-fade-in {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: translateY(0); }
}
.pn-fade-in {
  animation: pn-fade-in 0.4s var(--pn-ease) both;
}

/* ── Utility: stagger children ── */
.pn-stagger > * {
  animation: pn-fade-in 0.35s var(--pn-ease) both;
}
.pn-stagger > *:nth-child(1) { animation-delay: 0ms; }
.pn-stagger > *:nth-child(2) { animation-delay: 40ms; }
.pn-stagger > *:nth-child(3) { animation-delay: 80ms; }
.pn-stagger > *:nth-child(4) { animation-delay: 120ms; }
.pn-stagger > *:nth-child(5) { animation-delay: 160ms; }
.pn-stagger > *:nth-child(6) { animation-delay: 200ms; }
.pn-stagger > *:nth-child(7) { animation-delay: 240ms; }
.pn-stagger > *:nth-child(8) { animation-delay: 280ms; }

/* ── Utility: monospace label ── */
.pn-label {
  font-family: var(--pn-mono);
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--pn-text-muted);
}

/* ── Utility: section number ── */
.pn-section-num {
  font-family: var(--pn-serif);
  font-size: 11px;
  font-weight: 400;
  color: var(--pn-text-ghost);
  margin-right: var(--pn-space-2);
  font-style: italic;
}

/* ── Utility: accent pip ── */
.pn-accent-bar {
  width: 3px;
  background: var(--pn-accent);
  align-self: stretch;
  flex-shrink: 0;
}

/* ── Utility: status dot ── */
.pn-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}
.pn-dot--ok { background: var(--pn-ok); }
.pn-dot--warn { background: var(--pn-warn); }
.pn-dot--err { background: var(--pn-err); }
.pn-dot--info { background: var(--pn-info); }
.pn-dot--idle { background: var(--pn-border-strong); }

/* ── Utility: pulse animation for running states ── */
@keyframes pn-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
.pn-pulse {
  animation: pn-pulse 1.8s ease-in-out infinite;
}

/* ── Utility: typing cursor ── */
@keyframes pn-blink {
  0%, 50% { opacity: 1; }
  51%, 100% { opacity: 0; }
}
.pn-cursor::after {
  content: '█';
  font-size: 0.8em;
  animation: pn-blink 1s step-end infinite;
  color: var(--pn-accent);
  margin-left: 1px;
}
</style>
