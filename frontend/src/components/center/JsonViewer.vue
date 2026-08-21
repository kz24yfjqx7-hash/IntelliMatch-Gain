<template>
  <!-- JSON 高亮查看器：先转义再着色，避免 XSS -->
  <div class="json-viewer">
    <div class="json-toolbar">
      <span class="json-title">{{ title }}</span>
      <el-button link size="small" @click="copy">复制</el-button>
    </div>
    <pre class="json-pre" :style="{ maxHeight }" v-html="html"></pre>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { ElMessage } from 'element-plus'

const props = defineProps({
  value: { type: [Object, Array, String, Number, Boolean], default: null },
  title: { type: String, default: 'JSON' },
  maxHeight: { type: String, default: '420px' }
})

const text = computed(() => {
  try {
    return typeof props.value === 'string' ? props.value : JSON.stringify(props.value, null, 2)
  } catch {
    return String(props.value)
  }
})

function escapeHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

/** 逐 token 着色：键 / 字符串 / 数字 / 布尔 / null */
const html = computed(() => {
  const escaped = escapeHtml(text.value || '')
  return escaped.replace(
    /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)/g,
    match => {
      let cls = 'num'
      if (/^"/.test(match)) cls = /:$/.test(match) ? 'key' : 'str'
      else if (/true|false/.test(match)) cls = 'bool'
      else if (/null/.test(match)) cls = 'null'
      return `<span class="${cls}">${match}</span>`
    }
  )
})

async function copy() {
  try {
    if (navigator.clipboard) await navigator.clipboard.writeText(text.value)
    ElMessage.success('已复制')
  } catch {
    ElMessage.warning('复制失败，请手动选择复制')
  }
}
</script>

<style scoped>
.json-viewer { border: 1px solid rgba(0, 180, 216, .25); border-radius: 8px; background: rgba(0, 0, 0, .45); overflow: hidden; }
.json-toolbar { display: flex; justify-content: space-between; align-items: center; padding: 4px 10px; background: rgba(0, 180, 216, .08); font-size: 12px; color: var(--color-text-secondary); }
.json-pre { margin: 0; padding: 12px; overflow: auto; font-family: Consolas, 'Courier New', monospace; font-size: 12px; line-height: 1.6; color: #cdd6f4; white-space: pre-wrap; word-break: break-all; }
.json-pre :deep(.key) { color: #7dd3fc; }
.json-pre :deep(.str) { color: #a6e3a1; }
.json-pre :deep(.num) { color: #fab387; }
.json-pre :deep(.bool) { color: #cba6f7; }
.json-pre :deep(.null) { color: #f38ba8; }
</style>
