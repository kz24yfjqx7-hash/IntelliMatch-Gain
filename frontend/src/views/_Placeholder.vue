<template>
  <div class="placeholder">
    <div class="icon">🚧</div>
    <h2>{{ title }}</h2>
    <p>页面建设中</p>
  </div>
</template>

<script setup>
defineProps({ title: { type: String, default: '' } })
</script>

<style scoped>
.placeholder { height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 12px; color: var(--color-text-secondary); }
.icon { font-size: 48px; }
h2 { color: var(--color-primary); font-weight: 600; letter-spacing: 2px; }
</style>
