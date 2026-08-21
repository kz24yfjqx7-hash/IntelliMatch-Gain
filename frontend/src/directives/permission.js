/**
 * 按钮级权限指令。
 *   <el-button v-permission="'dispatch:issue'">下发</el-button>          无权限 → 移除元素
 *   <el-button v-permission.disable="'dispatch:issue'">下发</el-button>  无权限 → disabled
 *   v-permission="['asset:write','asset:export']"                        任一满足即可
 * 权限来自 stores/user 的 permissions（GET /auth/me）。
 * 响应式：permissions 变化（切换账号不刷新页面）时自动重新判定——
 *   移除模式用注释锚点占位，权限恢复后把元素插回原位；disable 模式切换 disabled。
 */
import { watch } from 'vue'
import { useUserStore } from '@/stores/user'

const STATE = new WeakMap() // el -> { anchor, stop }

function permLabel(value) {
  return Array.isArray(value) ? value.join(' / ') : String(value)
}

function apply(el, binding) {
  const userStore = useUserStore()
  const allowed = userStore.hasPermission(binding.value)
  const st = STATE.get(el) || {}
  if (binding.modifiers.disable) {
    if (allowed) {
      el.removeAttribute('disabled')
      el.classList.remove('is-disabled', 'perm-disabled')
      el.removeAttribute('title')
    } else {
      el.setAttribute('disabled', 'disabled')
      el.classList.add('is-disabled', 'perm-disabled')
      el.setAttribute('title', `需要权限：${permLabel(binding.value)}`)
    }
    return
  }
  if (allowed) {
    // 之前被移除：插回锚点位置
    if (st.anchor && st.anchor.parentNode && !el.parentNode) {
      st.anchor.parentNode.insertBefore(el, st.anchor)
    }
    el.style.display = ''
    return
  }
  if (el.parentNode) {
    if (!st.anchor) {
      st.anchor = document.createComment(`v-permission:${permLabel(binding.value)}`)
      STATE.set(el, st)
    }
    if (!st.anchor.parentNode) el.parentNode.insertBefore(st.anchor, el)
    el.parentNode.removeChild(el)
  } else {
    el.style.display = 'none'
  }
}

export const permission = {
  mounted(el, binding) {
    const userStore = useUserStore()
    const st = STATE.get(el) || {}
    // 权限列表变化时重新判定（同一 el 只建一个 watcher）
    st.stop = watch(() => userStore.permissions.slice(), () => apply(el, binding), { flush: 'post' })
    STATE.set(el, st)
    apply(el, binding)
  },
  updated: apply,
  unmounted(el) {
    const st = STATE.get(el)
    if (st?.stop) st.stop()
    if (st?.anchor?.parentNode) st.anchor.parentNode.removeChild(st.anchor)
    STATE.delete(el)
  }
}

export function setupPermissionDirective(app) {
  app.directive('permission', permission)
}

export default permission
