/**
 * 按钮级权限指令。
 *   <el-button v-permission="'dispatch:issue'">下发</el-button>          无权限 → 移除元素
 *   <el-button v-permission.disable="'dispatch:issue'">下发</el-button>  无权限 → disabled
 *   v-permission="['asset:write','asset:export']"                        任一满足即可
 * 权限来自 stores/user 的 permissions（GET /auth/me）。
 */
import { useUserStore } from '@/stores/user'

function apply(el, binding) {
  const userStore = useUserStore()
  const allowed = userStore.hasPermission(binding.value)
  if (allowed) {
    if (binding.modifiers.disable) {
      el.removeAttribute('disabled')
      el.classList.remove('is-disabled', 'perm-disabled')
    }
    return
  }
  if (binding.modifiers.disable) {
    el.setAttribute('disabled', 'disabled')
    el.classList.add('is-disabled', 'perm-disabled')
    el.setAttribute('title', `需要权限：${Array.isArray(binding.value) ? binding.value.join(' / ') : binding.value}`)
  } else if (el.parentNode) {
    el.parentNode.removeChild(el)
  } else {
    el.style.display = 'none'
  }
}

export const permission = {
  mounted: apply,
  updated: apply
}

export function setupPermissionDirective(app) {
  app.directive('permission', permission)
}

export default permission
