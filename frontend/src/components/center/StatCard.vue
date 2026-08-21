<template>
  <!-- 统计卡片：图标 + 数值 + 标签，tone 控制配色 -->
  <div class="stat-card" :class="[tone, { clickable }]" @click="$emit('click')">
    <div class="stat-icon">{{ icon }}</div>
    <div class="stat-info">
      <div class="stat-value">
        <span v-if="loading" class="stat-loading">…</span>
        <span v-else>{{ display }}</span>
        <span v-if="unit && !loading" class="stat-unit">{{ unit }}</span>
      </div>
      <div class="stat-label">{{ label }}</div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  icon: { type: String, default: '◆' },
  label: { type: String, default: '' },
  value: { type: [Number, String], default: '--' },
  unit: { type: String, default: '' },
  tone: { type: String, default: 'primary' }, // primary | success | warning | danger
  loading: { type: Boolean, default: false },
  clickable: { type: Boolean, default: false }
})
defineEmits(['click'])

const display = computed(() => {
  if (props.value === null || props.value === undefined || props.value === '') return '--'
  return typeof props.value === 'number' ? props.value.toLocaleString('zh-CN') : props.value
})
</script>

<style scoped>
.stat-card {
  display: flex; align-items: center; gap: 12px; padding: 14px 16px; border-radius: 10px;
  background: linear-gradient(135deg, rgba(10, 16, 24, .9), rgba(5, 10, 18, .95));
  border: 1px solid rgba(0, 180, 216, .2); transition: all .25s;
}
.stat-card.clickable { cursor: pointer; }
.stat-card.clickable:hover { transform: translateY(-2px); border-color: var(--color-primary); }
.stat-icon { font-size: 26px; width: 44px; height: 44px; display: flex; align-items: center; justify-content: center; border-radius: 10px; background: rgba(0, 180, 216, .1); }
.stat-value { font-size: 24px; font-weight: 700; color: var(--color-primary); line-height: 1.1; font-family: Consolas, 'Courier New', monospace; }
.stat-unit { font-size: 12px; margin-left: 4px; color: var(--color-text-secondary); font-weight: 400; }
.stat-label { font-size: 12px; color: var(--color-text-secondary); margin-top: 4px; }
.stat-loading { animation: pulse 1s infinite; }
.stat-card.success .stat-value { color: var(--color-success); }
.stat-card.success .stat-icon { background: rgba(46, 204, 113, .12); }
.stat-card.warning .stat-value { color: var(--color-warning); }
.stat-card.warning .stat-icon { background: rgba(243, 156, 18, .12); }
.stat-card.danger .stat-value { color: var(--color-danger); }
.stat-card.danger .stat-icon { background: rgba(230, 57, 70, .12); }
.stat-card.danger { border-color: rgba(230, 57, 70, .4); }
</style>
