<template>
  <span class="source-badge" :class="normalized" :title="title">
    <span class="dot"></span>{{ label }}
  </span>
</template>

<script setup>
/**
 * AI 解释来源徽章：live（真实大模型）/ cache（离线缓存）/ rule（规则模板）。
 * 契约 §2.10/2.11：explanationSource / source / narrativeSource 三处复用。
 */
import { computed } from 'vue'

const props = defineProps({
  source: { type: String, default: '' },
  prefix: { type: String, default: '' }
})

const LABELS = { live: 'DeepSeek 实时', cache: '离线缓存', rule: '规则模板' }
const normalized = computed(() => (['live', 'cache', 'rule'].includes(props.source) ? props.source : 'unknown'))
const label = computed(() => `${props.prefix}${LABELS[normalized.value] || (props.source || '未知来源')}`)
const title = computed(() => `来源：${props.source || '--'}`)
</script>

<style scoped>
.source-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  border: 1px solid rgba(136, 146, 176, 0.3);
  color: var(--color-text-secondary);
  white-space: nowrap;
}
.dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.source-badge.live { color: var(--color-success); border-color: rgba(46, 204, 113, 0.4); background: rgba(46, 204, 113, 0.12); }
.source-badge.cache { color: var(--color-warning); border-color: rgba(243, 156, 18, 0.4); background: rgba(243, 156, 18, 0.12); }
.source-badge.rule { color: var(--color-primary); border-color: rgba(0, 180, 216, 0.4); background: rgba(0, 180, 216, 0.12); }
</style>
