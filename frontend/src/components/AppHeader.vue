<template>
  <header class="header">
    <div class="header-left">
      <div class="logo">
        <span class="logo-icon">⚡</span>
        <span class="logo-text">能源可信数据空间平台</span>
        <span v-if="isMock" class="mock-badge" title="后端不可达时的离线演示桩（MSW）">离线演示模式</span>
      </div>
    </div>
    <div class="header-right">
      <div class="perspective-switch">
        <span class="switch-label">视角切换：</span>
        <div class="switch-container" @click="handleToggle">
          <div class="switch-track" :class="{ 'is-edge': !perspectiveStore.isCloud }">
            <div class="switch-thumb"></div>
          </div>
          <span class="switch-text cloud" :class="{ active: perspectiveStore.isCloud }">☁️ 云端</span>
          <span class="switch-text edge" :class="{ active: perspectiveStore.isEdge }">🖥️ 边端</span>
        </div>
      </div>

      <!-- 告警铃铛 -->
      <el-popover placement="bottom-end" :width="380" trigger="click" popper-class="alert-popper">
        <template #reference>
          <div class="bell" title="风险告警">
            <el-badge :value="logStore.unackedAlertCount" :hidden="logStore.unackedAlertCount === 0" :max="99">
              <el-icon :size="20"><Bell /></el-icon>
            </el-badge>
          </div>
        </template>
        <div class="alert-panel">
          <div class="alert-panel-title">
            <span>风险告警（未确认 {{ logStore.unackedAlertCount }}）</span>
            <el-button link size="small" @click="refreshAlerts">刷新</el-button>
          </div>
          <div v-if="logStore.alerts.length === 0" class="alert-empty">暂无告警</div>
          <div v-for="a in logStore.alerts.slice(0, 8)" :key="a.id" class="alert-item" :class="[a.riskLevel, { acked: a.status === 'acked' }]">
            <div class="alert-main">
              <span class="alert-code">{{ a.ruleCode }}</span>
              <span class="alert-level">{{ RISK_LABELS[a.riskLevel] || a.riskLevel }}</span>
              <span class="alert-time">{{ fmtTime(a.at || a.createdAt) }}</span>
            </div>
            <div class="alert-msg">{{ a.message }}</div>
            <div class="alert-actions">
              <span class="alert-did">{{ shortDid(a.actorDid) }}</span>
              <el-button v-if="a.status !== 'acked'" size="small" type="primary" link @click="ack(a)">确认</el-button>
              <span v-else class="alert-acked">已确认</span>
            </div>
          </div>
          <div class="alert-footer">
            <el-button link size="small" @click="router.push('/audit')">前往审计中心 →</el-button>
          </div>
        </div>
      </el-popover>

      <!-- 用户信息 -->
      <el-dropdown trigger="click" @command="onUserCommand">
        <div class="user-box">
          <el-icon :size="18"><UserFilled /></el-icon>
          <span class="user-name">{{ userStore.displayName || '未登录' }}</span>
          <span class="role-tag" :class="userStore.primaryRole">{{ userStore.roleLabel }}</span>
        </div>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item disabled>
              <div class="user-did">DID：{{ shortDid(userStore.did) || '--' }}</div>
            </el-dropdown-item>
            <el-dropdown-item command="identity">身份中心</el-dropdown-item>
            <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>
  </header>
</template>

<script setup>
import { useRouter } from 'vue-router'
import { Bell, UserFilled } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { usePerspectiveStore } from '@/stores/perspective'
import { useLogStore } from '@/stores/logs'
import { useUserStore } from '@/stores/user'
import { fmtTime, shortDid, RISK_LABELS } from '@/utils/format'

const router = useRouter()
const perspectiveStore = usePerspectiveStore()
const logStore = useLogStore()
const userStore = useUserStore()
const isMock = import.meta.env.VITE_USE_MOCK === 'true'

function handleToggle() {
  const oldPerspective = perspectiveStore.currentPerspective
  perspectiveStore.togglePerspective()
  const newPerspective = perspectiveStore.currentPerspective
  router.push(newPerspective === 'cloud' ? '/cloud/topology' : '/edge/classification')
  logStore.addLog(
    `视角切换：${oldPerspective === 'cloud' ? '云端' : '边端'} → ${newPerspective === 'cloud' ? '云端' : '边端'}`,
    'INFO',
    'SYSTEM'
  )
}

async function ack(alert) {
  try {
    await logStore.ackAlert(alert.id)
    ElMessage.success(`告警 ${alert.ruleCode} 已确认`)
  } catch {
    // request.js 已提示
  }
}

function refreshAlerts() {
  logStore.fetchAlerts({ status: 'open' }).catch(() => {})
}

async function onUserCommand(cmd) {
  if (cmd === 'logout') {
    await userStore.logout()
    logStore.addLog('用户退出登录', 'INFO', 'SYSTEM')
    router.replace('/login')
  } else if (cmd === 'identity') {
    router.push('/identity')
  }
}
</script>

<style scoped>
.header {
  height: var(--header-height);
  background: rgba(10, 16, 24, 0.8);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid rgba(0, 180, 216, 0.2);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  flex-shrink: 0;
}
.header-left { display: flex; align-items: center; }
.logo { display: flex; align-items: center; gap: 12px; }
.logo-icon { font-size: 28px; animation: pulse 2s infinite; }
.logo-text { font-size: 20px; font-weight: 600; color: var(--color-text); letter-spacing: 2px; }
.mock-badge {
  font-size: 11px; padding: 2px 8px; border-radius: 10px;
  border: 1px solid var(--color-warning); color: var(--color-warning);
  background: rgba(243, 156, 18, 0.1); letter-spacing: 1px;
}
.header-right { display: flex; align-items: center; gap: 20px; }
.perspective-switch { display: flex; align-items: center; gap: 12px; }
.switch-label { color: var(--color-text-secondary); font-size: 14px; }
.switch-container {
  display: flex; align-items: center; gap: 8px; cursor: pointer; padding: 8px 16px;
  background: var(--bg-tertiary); border-radius: 24px; border: 1px solid var(--color-primary); transition: all 0.3s ease;
}
.switch-container:hover { border-color: var(--color-secondary); box-shadow: 0 0 10px rgba(0, 180, 216, 0.3); }
.switch-track { width: 48px; height: 24px; background: var(--color-primary); border-radius: 12px; position: relative; transition: all 0.3s ease; }
.switch-track.is-edge { background: var(--color-warning); }
.switch-thumb { width: 20px; height: 20px; background: var(--color-text); border-radius: 50%; position: absolute; top: 2px; left: 2px; transition: all 0.3s ease; }
.switch-track.is-edge .switch-thumb { left: 26px; }
.switch-text { font-size: 14px; color: var(--color-text-secondary); transition: all 0.3s ease; }
.switch-text.active { color: var(--color-text); font-weight: 600; }

.bell { cursor: pointer; color: var(--color-text-secondary); display: flex; align-items: center; padding: 6px; border-radius: 50%; transition: all .2s; }
.bell:hover { color: var(--color-primary); background: rgba(0, 180, 216, 0.1); }

.user-box {
  display: flex; align-items: center; gap: 8px; cursor: pointer; color: var(--color-text);
  padding: 6px 12px; border-radius: 20px; border: 1px solid rgba(0, 180, 216, 0.25); background: rgba(0, 180, 216, 0.06);
}
.user-box:hover { border-color: var(--color-primary); }
.user-name { font-size: 14px; }
.role-tag { font-size: 11px; padding: 1px 8px; border-radius: 10px; background: rgba(0, 180, 216, 0.2); color: var(--color-primary); }
.role-tag.sys_admin { background: rgba(230, 57, 70, 0.2); color: #ff8a94; }
.role-tag.grid_dispatcher { background: rgba(46, 204, 113, 0.2); color: var(--color-success); }
.role-tag.regulator { background: rgba(243, 156, 18, 0.2); color: var(--color-warning); }
.user-did { font-size: 12px; color: #888; }

.alert-panel { max-height: 420px; overflow: auto; }
.alert-panel-title { display: flex; justify-content: space-between; align-items: center; font-weight: 600; margin-bottom: 8px; }
.alert-empty { color: #999; text-align: center; padding: 16px 0; }
.alert-item { padding: 8px 10px; border-radius: 6px; margin-bottom: 6px; border-left: 3px solid #999; background: rgba(0, 0, 0, 0.03); }
.alert-item.high, .alert-item.critical { border-left-color: #E63946; background: rgba(230, 57, 70, 0.06); }
.alert-item.medium { border-left-color: #F39C12; }
.alert-item.acked { opacity: 0.55; }
.alert-main { display: flex; gap: 8px; font-size: 12px; align-items: center; }
.alert-code { font-weight: 600; }
.alert-level { padding: 0 6px; border-radius: 8px; background: rgba(230, 57, 70, 0.15); color: #E63946; }
.alert-time { margin-left: auto; color: #999; }
.alert-msg { font-size: 13px; margin: 4px 0; }
.alert-actions { display: flex; justify-content: space-between; align-items: center; font-size: 12px; color: #999; }
.alert-acked { color: #2ECC71; }
.alert-footer { text-align: right; margin-top: 4px; }
</style>
