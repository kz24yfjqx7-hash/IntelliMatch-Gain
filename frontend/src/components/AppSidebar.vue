<template>
  <aside class="sidebar">
    <nav class="sidebar-nav">
      <div class="nav-section">
        <div class="section-title">
          <span class="section-icon">{{ perspectiveStore.isCloud ? '☁️' : '🖥️' }}</span>
          <span>{{ perspectiveStore.isCloud ? '云端中枢' : '边端节点' }}</span>
        </div>

        <div v-if="perspectiveStore.isEdge" class="node-selector">
          <div class="selector-label">选择节点</div>
          <div class="node-list">
            <div
              v-for="node in perspectiveStore.nodes"
              :key="node.id"
              class="node-option"
              :class="{ active: perspectiveStore.currentNode === node.id, [node.status]: true }"
              @click="selectNode(node.id)"
            >
              <span class="node-status-dot"></span>
              <span class="node-id">{{ node.id }}</span>
              <span class="node-name">{{ node.name }}</span>
              <span v-if="node.didStatus" class="did-dot" :class="node.didStatus" :title="`DID ${node.didStatus}`"></span>
            </div>
          </div>
        </div>

        <ul class="menu-list">
          <li
            v-for="menu in visibleCurrentMenus"
            :key="menu.path"
            class="menu-item"
            :class="{ active: isActive(menu.path), global: menu.path === '/audit' }"
            @click="navigateTo(menu)"
          >
            <span class="menu-icon">{{ getMenuIcon(menu.path) }}</span>
            <span class="menu-title">{{ menu.title }}</span>
            <span v-if="isActive(menu.path)" class="active-indicator"></span>
          </li>
        </ul>
      </div>

      <!-- 可信数据空间四个中心：两种视角都显示 -->
      <div class="nav-section center-section">
        <div class="section-title">
          <span class="section-icon">🛡️</span>
          <span>可信数据空间</span>
        </div>
        <ul class="menu-list">
          <li
            v-for="menu in visibleCenterMenus"
            :key="menu.path"
            class="menu-item"
            :class="{ active: isActive(menu.path) }"
            @click="navigateTo(menu)"
          >
            <span class="menu-icon">{{ getMenuIcon(menu.path) }}</span>
            <span class="menu-title">{{ menu.title }}</span>
            <span v-if="isActive(menu.path)" class="active-indicator"></span>
          </li>
        </ul>
      </div>
    </nav>
  </aside>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { usePerspectiveStore } from '@/stores/perspective'
import { useLogStore } from '@/stores/logs'
import { useUserStore } from '@/stores/user'

const router = useRouter()
const route = useRoute()
const perspectiveStore = usePerspectiveStore()
const logStore = useLogStore()
const userStore = useUserStore()

const iconMap = {
  '/cloud/topology': '🌐',
  '/cloud/aggregate': '🔄',
  '/edge/classification': '📊',
  '/edge/risk': '⚠️',
  '/edge/privacy': '🔐',
  '/edge/response': '✅',
  '/audit': '📋',
  '/identity': '🪪',
  '/assets': '🗂️',
  '/permission': '🔑',
  '/evidence': '⛓️'
}

/** 视角菜单：带 permission 的（边缘隐私计算 / 终端响应）按权限过滤，与路由 meta.permission 一致 */
const visibleCurrentMenus = computed(() =>
  perspectiveStore.currentMenus.filter(m => !m.permission || userStore.hasPermission(m.permission))
)

/** 无权限的中心菜单不显示 */
const visibleCenterMenus = computed(() =>
  perspectiveStore.centerMenus.filter(m => !m.permission || userStore.hasPermission(m.permission))
)

function getMenuIcon(path) {
  return iconMap[path] || '📄'
}

function isActive(path) {
  return route.path === path
}

function navigateTo(menu) {
  if (route.path !== menu.path) {
    router.push(menu.path)
    logStore.addLog(`进入模块：${menu.title}`, 'INFO', perspectiveStore.isCloud ? 'CLOUD' : 'EDGE')
  }
}

function selectNode(nodeId) {
  perspectiveStore.setCurrentNode(nodeId)
  const node = perspectiveStore.nodes.find(n => n.id === nodeId)
  logStore.addLog(`切换至节点：${node?.name || nodeId}`, 'INFO', 'EDGE')
}
</script>

<style scoped>
.sidebar {
  width: var(--sidebar-width);
  background: rgba(10, 16, 24, 0.7);
  backdrop-filter: blur(10px);
  border-right: 1px solid rgba(0, 180, 216, 0.15);
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
}
.sidebar-nav { flex: 1; padding: 16px 0; overflow-y: auto; }
.nav-section { padding: 0 12px; }
.center-section { margin-top: 12px; }
.section-title {
  display: flex; align-items: center; gap: 8px; padding: 12px 16px; color: var(--color-primary);
  font-size: 14px; font-weight: 600; border-bottom: 1px solid var(--bg-tertiary); margin-bottom: 8px;
}
.section-icon { font-size: 18px; }
.node-selector { padding: 12px; margin-bottom: 12px; background: rgba(0, 180, 216, 0.05); border-radius: 8px; border: 1px solid rgba(0, 180, 216, 0.1); }
.selector-label { font-size: 12px; color: var(--color-text-secondary); margin-bottom: 8px; padding-left: 4px; }
.node-list { display: flex; flex-direction: column; gap: 6px; }
.node-option { display: flex; align-items: center; gap: 8px; padding: 10px 12px; background: transparent; border-radius: 6px; cursor: pointer; transition: all 0.3s ease; border: 1px solid transparent; }
.node-option:hover { background: rgba(0, 180, 216, 0.1); border-color: rgba(0, 180, 216, 0.2); }
.node-option.active { background: rgba(0, 180, 216, 0.15); border-color: var(--color-primary); }
.node-status-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.node-option.online .node-status-dot { background: var(--color-success); box-shadow: 0 0 6px var(--color-success); }
.node-option.warning .node-status-dot { background: var(--color-warning); box-shadow: 0 0 6px var(--color-warning); }
.node-option.offline .node-status-dot { background: var(--color-danger); box-shadow: 0 0 6px var(--color-danger); }
.node-id { font-size: 12px; font-weight: 600; color: var(--color-text); min-width: 55px; }
.node-name { font-size: 11px; color: var(--color-text-secondary); flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.did-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--color-success); }
.did-dot.frozen { background: var(--color-warning); }
.did-dot.revoked { background: var(--color-danger); }
.menu-list { list-style: none; }
.menu-item { display: flex; align-items: center; gap: 12px; padding: 14px 16px; margin: 4px 0; border-radius: 8px; cursor: pointer; transition: all 0.3s ease; position: relative; color: var(--color-text-secondary); }
.menu-item:hover { background: var(--bg-tertiary); color: var(--color-text); }
.menu-item.active { background: linear-gradient(90deg, rgba(0, 180, 216, 0.2) 0%, transparent 100%); color: var(--color-primary); border-left: 3px solid var(--color-primary); }
.menu-item.global { margin-top: 16px; border-top: 1px solid var(--bg-tertiary); padding-top: 20px; }
.menu-icon { font-size: 18px; width: 24px; text-align: center; }
.menu-title { font-size: 14px; flex: 1; }
.active-indicator { width: 6px; height: 6px; background: var(--color-primary); border-radius: 50%; animation: pulse 1.5s infinite; }
</style>
