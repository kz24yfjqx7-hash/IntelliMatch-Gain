<template>
  <!-- 哈希 / DID 缩略显示，点击复制完整值 -->
  <span class="hash-text" :title="value || ''" @click.stop="copy">{{ shown }}</span>
</template>

<script setup>
import { computed } from 'vue'
import { ElMessage } from 'element-plus'
import { shortHash } from '@/utils/format'

const props = defineProps({
  value: { type: String, default: '' },
  head: { type: Number, default: 10 },
  tail: { type: Number, default: 6 }
})
const shown = computed(() => shortHash(props.value, props.head, props.tail))

async function copy() {
  if (!props.value) return
  try {
    if (navigator.clipboard) await navigator.clipboard.writeText(props.value)
    ElMessage.success('已复制到剪贴板')
  } catch {
    ElMessage.warning('复制失败')
  }
}
</script>

<style scoped>
.hash-text { font-family: Consolas, 'Courier New', monospace; font-size: 12px; color: #7dd3fc; cursor: copy; }
.hash-text:hover { text-decoration: underline; }
</style>
