<template>
  <!-- 四个中心页的统一外壳：标题栏 + 操作区 + 内容；深色科技风 -->
  <div class="center-page">
    <div class="center-header">
      <div class="center-title-box">
        <span class="center-icon">{{ icon }}</span>
        <div>
          <h2 class="center-title glow-text">{{ title }}</h2>
          <p v-if="desc" class="center-desc">{{ desc }}</p>
        </div>
      </div>
      <div class="center-actions">
        <slot name="actions" />
      </div>
    </div>
    <div class="center-body">
      <slot />
    </div>
  </div>
</template>

<script setup>
defineProps({
  icon: { type: String, default: '◆' },
  title: { type: String, required: true },
  desc: { type: String, default: '' }
})
</script>

<style scoped>
.center-page { display: flex; flex-direction: column; gap: 16px; min-height: 100%; animation: fadeIn .4s ease-out; }
.center-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.center-title-box { display: flex; align-items: center; gap: 14px; }
.center-icon { font-size: 34px; filter: drop-shadow(0 0 8px rgba(0, 180, 216, .6)); }
.center-title { font-size: 22px; font-weight: 600; letter-spacing: 2px; color: var(--color-text); }
.center-desc { margin-top: 4px; font-size: 12px; color: var(--color-text-secondary); letter-spacing: 1px; }
.center-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.center-body { display: flex; flex-direction: column; gap: 16px; }
</style>

<style>
/* ---------- 中心页通用深色皮肤（非 scoped，限定在 .center-page 内） ---------- */
.center-page .panel {
  position: relative; padding: 16px 18px; border-radius: 10px;
  background: linear-gradient(135deg, rgba(10, 16, 24, .9), rgba(5, 10, 18, .95));
  border: 1px solid rgba(0, 180, 216, .2);
  box-shadow: 0 0 20px rgba(0, 180, 216, .08), inset 0 1px 0 rgba(255, 255, 255, .04);
}
.center-page .panel.danger { border-color: rgba(230, 57, 70, .7); box-shadow: 0 0 24px rgba(230, 57, 70, .25); }
.center-page .panel-title {
  display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap;
  margin-bottom: 12px; color: var(--color-primary); font-weight: 600; font-size: 14px; letter-spacing: 1px;
}
.center-page .panel-title .sub { font-weight: 400; font-size: 12px; color: var(--color-text-secondary); }
.center-page .grid-2 { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.center-page .grid-3 { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }
.center-page .stat-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
.center-page .toolbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
.center-page .toolbar .spacer { flex: 1; }
.center-page .mono { font-family: Consolas, 'Courier New', monospace; font-size: 12px; }
.center-page .muted { color: var(--color-text-secondary); }
.center-page .pager { display: flex; justify-content: flex-end; margin-top: 12px; }
.center-page .empty-tip { padding: 24px; text-align: center; color: var(--color-text-secondary); font-size: 13px; }
@media (max-width: 1100px) {
  .center-page .grid-2, .center-page .grid-3 { grid-template-columns: 1fr; }
}

/* Element Plus 深色适配 */
.center-page .el-tabs__item { color: var(--color-text-secondary); }
.center-page .el-tabs__item.is-active { color: var(--color-primary); }
.center-page .el-tabs__active-bar { background: var(--color-primary); }
.center-page .el-tabs__nav-wrap::after { background: rgba(0, 180, 216, .15); }
.center-page .el-form-item__label { color: var(--color-text-secondary); }
.center-page .el-input__wrapper, .center-page .el-textarea__inner, .center-page .el-select__wrapper {
  background: rgba(0, 0, 0, .35); box-shadow: 0 0 0 1px rgba(0, 180, 216, .25) inset; color: var(--color-text);
}
.center-page .el-input__inner, .center-page .el-textarea__inner, .center-page .el-select__selected-item { color: var(--color-text); }
.center-page .el-input-number .el-input-number__decrease, .center-page .el-input-number .el-input-number__increase { background: rgba(0, 180, 216, .1); color: var(--color-text); }
.center-page .el-pagination { --el-pagination-bg-color: transparent; --el-pagination-button-bg-color: rgba(0, 180, 216, .08); --el-pagination-text-color: var(--color-text-secondary); --el-pagination-button-disabled-bg-color: transparent; }
.center-page .el-pagination .el-pager li { background: rgba(0, 180, 216, .08); color: var(--color-text-secondary); }
.center-page .el-pagination .el-pager li.is-active { background: var(--color-primary); color: #fff; }
.center-page .el-descriptions { --el-descriptions-item-bordered-label-background: rgba(0, 180, 216, .08); }
.center-page .el-descriptions__body { background: transparent; color: var(--color-text); }
.center-page .el-descriptions__label { color: var(--color-text-secondary) !important; }
.center-page .el-descriptions__content { color: var(--color-text) !important; word-break: break-all; }
.center-page .el-descriptions .el-descriptions__cell { border-color: rgba(0, 180, 216, .15) !important; }
.center-page .el-timeline-item__content { color: var(--color-text); }
.center-page .el-timeline-item__timestamp { color: var(--color-text-secondary); }
.center-page .el-timeline-item__tail { border-color: rgba(0, 180, 216, .3); }
.center-page .el-radio, .center-page .el-checkbox { color: var(--color-text-secondary); }
.center-page .el-checkbox__label, .center-page .el-radio__label { color: var(--color-text-secondary); }
.center-page .el-table .cell { word-break: break-all; }
.center-page .el-table .tampered-row td { background: rgba(230, 57, 70, .18) !important; }
.center-page .el-table .affected-row td { background: rgba(243, 156, 18, .12) !important; }
.center-page .el-table__empty-text { color: var(--color-text-secondary); }
.center-page .el-alert { background: rgba(0, 180, 216, .08); }
.center-page .el-collapse { border-color: rgba(0, 180, 216, .15); }
.center-page .el-collapse-item__header, .center-page .el-collapse-item__wrap { background: transparent; color: var(--color-text); border-color: rgba(0, 180, 216, .15); }
.center-page .el-step__title, .center-page .el-step__description { color: var(--color-text-secondary); }

/* 对话框 / 抽屉内同样需要深色表单（它们被 teleport 到 body，故单独写） */
.center-dialog .el-form-item__label { color: var(--color-text-secondary); }
.center-dialog .el-input__wrapper, .center-dialog .el-textarea__inner, .center-dialog .el-select__wrapper {
  background: rgba(0, 0, 0, .35); box-shadow: 0 0 0 1px rgba(0, 180, 216, .25) inset;
}
.center-dialog .el-input__inner, .center-dialog .el-textarea__inner, .center-dialog .el-select__selected-item { color: var(--color-text); }
.center-dialog .el-dialog__body, .center-dialog .el-drawer__body { color: var(--color-text); }
.center-dialog .el-descriptions__body { background: transparent; }
.center-dialog .el-descriptions__label { color: var(--color-text-secondary) !important; background: rgba(0, 180, 216, .08) !important; }
.center-dialog .el-descriptions__content { color: var(--color-text) !important; word-break: break-all; }
.center-dialog .el-descriptions .el-descriptions__cell { border-color: rgba(0, 180, 216, .15) !important; }
.center-dialog .el-checkbox__label, .center-dialog .el-radio__label, .center-dialog .el-radio, .center-dialog .el-checkbox { color: var(--color-text-secondary); }
.center-dialog .el-timeline-item__content { color: var(--color-text); }
.center-dialog .el-timeline-item__timestamp { color: var(--color-text-secondary); }
.center-dialog .el-alert { background: rgba(0, 180, 216, .08); }
.center-dialog .el-divider__text { background: var(--bg-secondary); color: var(--color-text-secondary); }
.center-dialog .el-divider { border-color: rgba(0, 180, 216, .2); }
.center-dialog .mono { font-family: Consolas, 'Courier New', monospace; font-size: 12px; }
.center-dialog .muted { color: var(--color-text-secondary); }
.center-dialog .key-box {
  padding: 10px 12px; border-radius: 8px; margin: 6px 0; word-break: break-all;
  background: rgba(0, 0, 0, .4); border: 1px solid rgba(0, 180, 216, .25); font-family: Consolas, monospace; font-size: 12px;
}
.center-dialog .key-box.secret { border-color: var(--color-warning); color: var(--color-warning); }

/* 下拉面板（teleport 到 body） */
.center-popper.el-popper, .center-popper .el-select-dropdown__item { background: var(--bg-secondary); color: var(--color-text); }
.center-popper.el-popper { border-color: rgba(0, 180, 216, .3); }
.center-popper .el-select-dropdown__item.is-hovering, .center-popper .el-select-dropdown__item:hover { background: rgba(0, 180, 216, .15); }
.center-popper .el-select-dropdown__item.is-selected { color: var(--color-primary); }
</style>
