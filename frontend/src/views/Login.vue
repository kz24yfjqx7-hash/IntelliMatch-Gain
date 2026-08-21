<template>
  <div class="login-page">
    <div class="bg-grid"></div>
    <div class="glow glow-a"></div>
    <div class="glow glow-b"></div>

    <div class="login-card">
      <div class="brand">
        <span class="brand-icon">⚡</span>
        <div>
          <h1>能源可信数据空间平台</h1>
          <p>DID 身份 · 隐私计算 · 区块链存证 · 智能调度</p>
        </div>
      </div>

      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" size="large" @submit.prevent="submit">
        <el-form-item label="账号" prop="username">
          <el-input v-model="form.username" placeholder="请输入账号" autocomplete="username" :prefix-icon="User" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input v-model="form.password" type="password" show-password placeholder="请输入密码" autocomplete="current-password" :prefix-icon="Lock" @keyup.enter="submit" />
        </el-form-item>
        <el-button type="primary" class="submit-btn" :loading="userStore.loading" native-type="submit">
          {{ userStore.loading ? '身份校验中…' : '登 录' }}
        </el-button>
      </el-form>

      <div class="demo-accounts">
        <div class="demo-title">演示账号一键填充</div>
        <div class="demo-grid">
          <button
            v-for="acc in DEMO_ACCOUNTS"
            :key="acc.username"
            type="button"
            class="demo-btn"
            :class="{ active: form.username === acc.username }"
            @click="fill(acc)"
          >
            <span class="demo-role">{{ acc.label }}</span>
            <span class="demo-user">{{ acc.username }}</span>
          </button>
        </div>
      </div>

      <div class="footer">
        <span v-if="isMock" class="mock-badge">离线演示模式（Mock）</span>
        <span v-else class="live-badge">已连接后端 {{ apiBase }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { User, Lock } from '@element-plus/icons-vue'
import { useUserStore } from '@/stores/user'
import { useLogStore } from '@/stores/logs'

const router = useRouter()
const route = useRoute()
const userStore = useUserStore()
const logStore = useLogStore()
const isMock = import.meta.env.VITE_USE_MOCK === 'true'
const apiBase = import.meta.env.VITE_API_BASE || '/api/v1'

/** 与 contract/DB-SCHEMA.md 种子账号一致 */
const DEMO_ACCOUNTS = [
  { username: 'admin', password: 'admin123', label: '系统管理员' },
  { username: 'grid', password: 'grid123', label: '电网调度员' },
  { username: 'vpp', password: 'vpp123', label: '虚拟电厂运营商' },
  { username: 'subject', password: 'subject123', label: '能源主体' },
  { username: 'regulator', password: 'reg123', label: '监管方' },
  { username: 'edge', password: 'edge123', label: '边缘节点' }
]

const formRef = ref()
const form = reactive({ username: 'admin', password: 'admin123' })
const rules = {
  username: [{ required: true, message: '请输入账号', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }]
}

function fill(acc) {
  form.username = acc.username
  form.password = acc.password
}

/** 登录后按角色跳转：sys_admin/grid → 驾驶舱；edge → 边端分级页；其余 → 驾驶舱 */
function targetAfterLogin() {
  const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : ''
  if (redirect && redirect !== '/login') return redirect
  return userStore.homePath()
}

async function submit() {
  try {
    await formRef.value?.validate()
  } catch {
    return
  }
  try {
    const data = await userStore.login({ username: form.username, password: form.password })
    logStore.addLog(`用户 ${data.user.realName}（${data.user.roles.join(',')}）登录成功`, 'INFO', 'AUTH')
    ElMessage.success(`欢迎，${data.user.realName}`)
    router.replace(targetAfterLogin())
  } catch (err) {
    // request.js 已 toast；这里记录日志
    logStore.addLog(`登录失败：${err?.message || '未知错误'}`, 'ERROR', 'AUTH')
  }
}
</script>

<style scoped>
.login-page {
  position: relative; width: 100%; height: 100%; display: flex; align-items: center; justify-content: center;
  background: radial-gradient(ellipse at 20% 20%, #0a1f33 0%, var(--bg-primary) 55%);
  overflow: hidden;
}
.bg-grid {
  position: absolute; inset: 0;
  background-image: linear-gradient(rgba(0, 180, 216, 0.08) 1px, transparent 1px), linear-gradient(90deg, rgba(0, 180, 216, 0.08) 1px, transparent 1px);
  background-size: 40px 40px;
  mask-image: radial-gradient(ellipse at center, #000 30%, transparent 75%);
}
.glow { position: absolute; border-radius: 50%; filter: blur(80px); opacity: 0.35; }
.glow-a { width: 420px; height: 420px; background: #00B4D8; top: -120px; right: -80px; }
.glow-b { width: 360px; height: 360px; background: #0077B6; bottom: -140px; left: -60px; }

.login-card {
  position: relative; width: 460px; padding: 36px 40px 24px;
  background: rgba(10, 16, 24, 0.85); border: 1px solid rgba(0, 180, 216, 0.3); border-radius: 14px;
  box-shadow: 0 0 40px rgba(0, 180, 216, 0.15), inset 0 0 60px rgba(0, 180, 216, 0.03); backdrop-filter: blur(12px);
}
.brand { display: flex; align-items: center; gap: 14px; margin-bottom: 28px; }
.brand-icon { font-size: 40px; animation: pulse 2s infinite; }
.brand h1 { font-size: 22px; color: var(--color-text); letter-spacing: 2px; font-weight: 600; }
.brand p { font-size: 12px; color: var(--color-text-secondary); letter-spacing: 1px; margin-top: 4px; }
.submit-btn { width: 100%; margin-top: 6px; letter-spacing: 6px; font-weight: 600; }

.demo-accounts { margin-top: 22px; padding-top: 16px; border-top: 1px dashed rgba(0, 180, 216, 0.2); }
.demo-title { font-size: 12px; color: var(--color-text-secondary); margin-bottom: 10px; }
.demo-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.demo-btn {
  display: flex; flex-direction: column; align-items: flex-start; gap: 2px; padding: 8px 10px; cursor: pointer;
  background: rgba(0, 180, 216, 0.05); border: 1px solid rgba(0, 180, 216, 0.2); border-radius: 8px; color: var(--color-text); transition: all .2s;
}
.demo-btn:hover, .demo-btn.active { border-color: var(--color-primary); background: rgba(0, 180, 216, 0.15); }
.demo-role { font-size: 12px; }
.demo-user { font-size: 11px; color: var(--color-text-secondary); font-family: Consolas, monospace; }

.footer { margin-top: 18px; text-align: center; }
.mock-badge { font-size: 12px; padding: 3px 12px; border-radius: 12px; border: 1px solid var(--color-warning); color: var(--color-warning); background: rgba(243, 156, 18, 0.1); }
.live-badge { font-size: 12px; padding: 3px 12px; border-radius: 12px; border: 1px solid var(--color-success); color: var(--color-success); }

:deep(.el-form-item__label) { color: var(--color-text-secondary); }
:deep(.el-input__wrapper) { background: rgba(0, 0, 0, 0.35); box-shadow: 0 0 0 1px rgba(0, 180, 216, 0.25) inset; }
:deep(.el-input__inner) { color: var(--color-text); }
</style>
