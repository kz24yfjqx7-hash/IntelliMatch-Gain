<template>
  <div class="flow-anim" :class="{ running }">
    <div v-for="(stage, i) in STAGES" :key="stage.key" class="flow-stage" :class="{ active: activeIndex === i, done: activeIndex > i }">
      <div class="stage-icon">{{ stage.icon }}</div>
      <div class="stage-title">{{ stage.title }}</div>
      <div class="stage-desc">{{ stage.desc }}</div>
      <div v-if="i < STAGES.length - 1" class="stage-link">
        <span class="packet" v-for="n in 3" :key="n" :style="{ animationDelay: `${(n - 1) * 0.5}s` }"></span>
      </div>
      <div v-if="i === 0" class="lock-tag">原始数据不出域</div>
    </div>
  </div>
</template>

<script setup>
/**
 * 「数据不出域」流程动画：节点本地训练 → 梯度裁剪加噪 → Top-k 稀疏 → 仅上传参数 → 云端聚合。
 * 纯 CSS 动画；running=true 时数据包沿连线流动，activeIndex 高亮当前环节（由 fl_progress 驱动轮转）。
 */
const STAGES = [
  { key: 'local', icon: '🏭', title: '节点本地训练', desc: '原始负荷/光伏数据留在边缘' },
  { key: 'dp', icon: '🎲', title: '梯度裁剪加噪', desc: 'L2 裁剪 + 高斯噪声（ε,δ）' },
  { key: 'topk', icon: '✂️', title: 'Top-k 稀疏', desc: '仅保留 k% 最大梯度' },
  { key: 'upload', icon: '📡', title: '仅上传参数', desc: '哈希上链可追溯' },
  { key: 'agg', icon: '☁️', title: '云端 FedAvg 聚合', desc: '按样本量加权平均' }
]

defineProps({
  running: { type: Boolean, default: false },
  activeIndex: { type: Number, default: -1 }
})
</script>

<style scoped>
.flow-anim {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 18px;
  padding: 12px 4px 4px;
}
.flow-stage {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 14px 8px 12px;
  border-radius: 10px;
  border: 1px solid rgba(0, 180, 216, 0.15);
  background: rgba(0, 180, 216, 0.03);
  text-align: center;
  transition: all 0.3s ease;
}
.flow-stage.active { border-color: var(--color-primary); box-shadow: 0 0 16px rgba(0, 180, 216, 0.35); background: rgba(0, 180, 216, 0.1); }
.flow-stage.done { border-color: rgba(46, 204, 113, 0.4); }
.stage-icon { font-size: 26px; }
.flow-stage.active .stage-icon { animation: pulse 1.2s infinite; }
.stage-title { font-size: 13px; font-weight: 600; color: var(--color-text); }
.stage-desc { font-size: 11px; color: var(--color-text-secondary); }
.stage-link {
  position: absolute;
  right: -18px;
  top: 50%;
  width: 18px;
  height: 2px;
  background: rgba(0, 180, 216, 0.25);
  overflow: visible;
}
.packet {
  position: absolute;
  top: -3px;
  left: 0;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-primary);
  box-shadow: 0 0 8px var(--color-primary);
  opacity: 0;
}
.flow-anim.running .packet { animation: travel 1.5s linear infinite; }
@keyframes travel {
  0% { transform: translateX(-4px); opacity: 0; }
  15% { opacity: 1; }
  85% { opacity: 1; }
  100% { transform: translateX(16px); opacity: 0; }
}
.lock-tag {
  position: absolute;
  top: -10px;
  left: 50%;
  transform: translateX(-50%);
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 10px;
  color: var(--color-danger);
  background: rgba(230, 57, 70, 0.15);
  border: 1px solid rgba(230, 57, 70, 0.4);
  white-space: nowrap;
}
@media (max-width: 1100px) {
  .flow-anim { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .stage-link { display: none; }
}
</style>
