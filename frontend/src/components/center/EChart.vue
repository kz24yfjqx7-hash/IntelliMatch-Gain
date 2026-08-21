<template>
  <!-- ECharts 薄封装：option 变化自动 setOption，容器尺寸变化自动 resize -->
  <div ref="el" class="echart" :style="{ height }"></div>
</template>

<script setup>
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  option: { type: Object, default: () => ({}) },
  height: { type: String, default: '240px' }
})
const emit = defineEmits(['click'])

const el = ref(null)
let chart = null
let ro = null

function render() {
  if (!chart || !props.option) return
  try {
    chart.setOption(props.option, true)
  } catch {
    // option 非法时忽略，避免整页崩溃
  }
}

onMounted(() => {
  try {
    chart = echarts.init(el.value, null, { renderer: 'canvas' })
    chart.on('click', p => emit('click', p))
    render()
    if (typeof ResizeObserver !== 'undefined') {
      ro = new ResizeObserver(() => chart && chart.resize())
      ro.observe(el.value)
    }
  } catch {
    chart = null // jsdom 等无 canvas 环境
  }
})
watch(() => props.option, render, { deep: true })
onBeforeUnmount(() => {
  if (ro) ro.disconnect()
  if (chart) chart.dispose()
  chart = null
})
</script>

<style scoped>
.echart { width: 100%; min-height: 120px; }
</style>
