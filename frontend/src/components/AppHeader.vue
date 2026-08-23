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

      <!-- 消息铃铛：待办消息 + 风险告警 -->
      <el-popover placement="bottom-end" :width="400" trigger="click" popper-class="alert-popper"
                  @show="onBellOpen">
        <template #reference>
          <div class="bell" :title="bellTitle">
            <el-badge :value="bellCount" :hidden="bellCount === 0" :max="99">
              <el-icon :size="20"><Bell /></el-icon>
            </el-badge>
          </div>
        </template>

        <div class="bell-tabs">
          <div class="bell-tab" :class="{ active: bellTab === 'notice' }" @click="bellTab = 'notice'">
            消息<span v-if="noticeStore.unreadCount" class="tab-dot">{{ noticeStore.unreadCount }}</span>
          </div>
          <div v-if="userStore.canReadAudit" class="bell-tab" :class="{ active: bellTab === 'alert' }" @click="bellTab = 'alert'">
            风险告警<span v-if="logStore.unackedAlertCount" class="tab-dot">{{ logStore.unackedAlertCount }}</span>
          </div>
        </div>

        <!-- 消息 -->
        <div v-show="bellTab === 'notice'" class="alert-panel">
          <div class="alert-panel-title">
            <span>消息（未读 {{ noticeStore.unreadCount }}）</span>
            <span>
              <el-button link size="small" :disabled="noticeStore.unreadCount === 0 || noticeStore.available === false" @click="readAll">全部已读</el-button>
              <el-button link size="small" @click="refreshNotices">刷新</el-button>
            </span>
          </div>
          <div v-if="noticeStore.available === false" class="alert-empty">
            消息服务未就绪<br />
            <span class="empty-hint">后端需重启并执行 sql/05_migrate_20260823.sql</span>
          </div>
          <div v-else-if="noticeStore.notices.length === 0" class="alert-empty">暂无消息</div>
          <div v-for="n in noticeStore.notices.slice(0, 10)" :key="n.id"
               class="notice-item" :class="[n.level, { read: n.status === 'read' }]"
               @click="openNotice(n)">
            <div class="notice-head">
              <span class="notice-dot" :class="{ hidden: n.status === 'read' }"></span>
              <span class="notice-title">{{ n.title }}</span>
              <span class="alert-time">{{ fmtTime(n.createdAt) }}</span>
            </div>
            <div class="notice-content">{{ n.content }}</div>
            <div class="notice-foot">
              <span>{{ n.actorName || shortDid(n.actorDid) || '系统' }}</span>
              <span class="notice-go">查看 →</span>
            </div>
          </div>
          <div class="alert-footer">
            <el-button link size="small" @click="goPermission">前往权限中心 →</el-button>
          </div>
        </div>

        <!-- 风险告警（保持原样） -->
        <div v-show="bellTab === 'alert'" class="alert-panel">
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
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { Bell, UserFilled } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { usePerspectiveStore } from '@/stores/perspective'
import { useLogStore } from '@/stores/logs'
import { useNoticeStore } from '@/stores/notice'
import { useUserStore } from '@/stores/user'
import { fmtTime, shortDid, RISK_LABELS } from '@/utils/format'

const router = useRouter()
const perspectiveStore = usePerspectiveStore()
const logStore = useLogStore()
const noticeStore = useNoticeStore()
const userStore = useUserStore()
const isMock = import.meta.env.VITE_USE_MOCK === 'true'

const bellTab = ref('notice')
// 角标是两类未加起来的总数：用户只关心「有几件事等我」，不关心分类
const bellCount = computed(() => noticeStore.unreadCount + logStore.unackedAlertCount)
const bellTitle = computed(() => {
  const parts = []
  if (noticeStore.unreadCount) parts.push(`${noticeStore.unreadCount} 条未读消息`)
  if (logStore.unackedAlertCount) parts.push(`${logStore.unackedAlertCount} 条未确认告警`)
  return parts.length ? parts.join('，') : '消息与告警'
})

/** 打开铃铛时拉一次最新的，并把默认 tab 落在有未读的那一侧 */
function onBellOpen() {
  if (noticeStore.unreadCount === 0 && logStore.unackedAlertCount > 0 && userStore.canReadAudit) {
    bellTab.value = 'alert'
  }
  refreshNotices()
}

function refreshNotices() {
  noticeStore.fetchNotices().catch(() => {})
}

async function readAll() {
  try {
    await noticeStore.markAllRead()
  } catch { /* request.js 已提示 */ }
}

/** 点一条消息：标记已读并跳到它指向的地方 */
async function openNotice(n) {
  if (n.status === 'unread') {
    noticeStore.markRead([n.id]).catch(() => {})
  }
  if (!n.link) return
  // link 形如 /permission?tab=applications&id=12
  const [path, query = ''] = String(n.link).split('?')
  const q = Object.fromEntries(new URLSearchParams(query))
  router.push({ path, query: q })
}

function goPermission() {
  router.push('/permission')
}

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
  if (!userStore.canReadAudit) return
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

.bell-tabs { display: flex; gap: 4px; border-bottom: 1px solid rgba(128,128,128,.25); margin-bottom: 8px; }
.bell-tab {
  padding: 6px 12px; cursor: pointer; font-size: 13px; color: var(--color-text-secondary, #888);
  border-bottom: 2px solid transparent; display: flex; align-items: center; gap: 6px;
}
.bell-tab:hover { color: var(--color-primary, #00B4D8); }
.bell-tab.active { color: var(--color-primary, #00B4D8); border-bottom-color: var(--color-primary, #00B4D8); font-weight: 600; }
.tab-dot {
  min-width: 16px; height: 16px; line-height: 16px; padding: 0 4px; border-radius: 8px;
  background: #E63946; color: #fff; font-size: 11px; text-align: center;
}

.notice-item {
  padding: 8px 10px; border-radius: 6px; margin-bottom: 6px; cursor: pointer;
  border-left: 3px solid var(--color-primary, #00B4D8); background: rgba(0, 180, 216, 0.06);
  transition: background .15s;
}
.notice-item:hover { background: rgba(0, 180, 216, 0.14); }
.notice-item.success { border-left-color: #2ECC71; background: rgba(46, 204, 113, 0.07); }
.notice-item.warning { border-left-color: #F39C12; background: rgba(243, 156, 18, 0.07); }
.notice-item.read { opacity: .55; }
.notice-head { display: flex; align-items: center; gap: 6px; font-size: 13px; }
.notice-dot { width: 7px; height: 7px; border-radius: 50%; background: #E63946; flex-shrink: 0; }
.notice-dot.hidden { visibility: hidden; }
.notice-title { font-weight: 600; }
.notice-content {
  font-size: 12px; margin: 4px 0 2px 13px; line-height: 1.5;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.notice-foot { display: flex; justify-content: space-between; font-size: 11px; color: #999; margin-left: 13px; }
.notice-go { color: var(--color-primary, #00B4D8); }

.alert-panel { max-height: 420px; overflow: auto; }
.alert-panel-title { display: flex; justify-content: space-between; align-items: center; font-weight: 600; margin-bottom: 8px; }
.alert-empty { color: #999; text-align: center; padding: 16px 0; }
.empty-hint { font-size: 11px; color: #777; }
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
