<template>
  <div class="app-container">
    <AppHeader />
    <div class="main-wrapper">
      <AppSidebar />
      <main class="content-area">
        <router-view v-slot="{ Component }">
          <transition name="fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </main>
    </div>
    <AppLogBar />
  </div>
</template>

<script setup>
/**
 * 主布局：顶栏 + 侧栏 + 内容区 + 底部日志栏（原 App.vue 的布局抽取至此）。
 * 进入布局时：订阅 WS 日志流、拉节点列表、拉未确认告警。
 */
import { onMounted, onBeforeUnmount, watch } from 'vue'
import { useRoute } from 'vue-router'
import AppHeader from '@/components/AppHeader.vue'
import AppSidebar from '@/components/AppSidebar.vue'
import AppLogBar from '@/components/AppLogBar.vue'
import { useLogStore } from '@/stores/logs'
import { usePerspectiveStore } from '@/stores/perspective'
import { useUserStore } from '@/stores/user'
import { wsClient } from '@/api/ws'

const route = useRoute()
const logStore = useLogStore()
const perspectiveStore = usePerspectiveStore()
const userStore = useUserStore()

onMounted(async () => {
  logStore.attachWs()
  if (userStore.token) wsClient.connect(userStore.token)
  try {
    await perspectiveStore.fetchNodes()
  } catch {
    logStore.addLog('节点列表拉取失败，使用本地种子数据', 'WARN', 'SYSTEM')
  }
  // 真后端 /audit/alerts 仅 sys_admin/regulator 可读（其它角色 1003 且会被风控计数），按角色门控；告警仍通过 WS audit_alert 实时到达
  if (userStore.canReadAudit) logStore.fetchAlerts({ status: 'open' }).catch(() => {})
})

// 离开布局（退出登录回到 /login）时清理 WS 订阅，避免重复订阅与泄漏
onBeforeUnmount(() => {
  logStore.detachWs()
  perspectiveStore.stopNodePolling()
})

// 根据路由 meta.perspective 同步视角（中心页与审计页不改变视角）
watch(() => route.meta.perspective, p => {
  if (p === 'cloud' || p === 'edge') perspectiveStore.currentPerspective = p
}, { immediate: true })
</script>

<style scoped>
.app-container {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  background: transparent;
}
.main-wrapper {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  overflow: hidden;
  background: transparent;
}
.content-area {
  flex: 1;
  min-width: 0;
  min-height: 0;
  padding: 20px;
  overflow: auto;
  background: transparent;
}
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.3s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
