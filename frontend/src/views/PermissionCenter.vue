<template>
  <CenterPage icon="🛡️" title="权限控制中心" desc="RBAC 角色 · 资源-操作矩阵 · 申请审批留痕 · 授权回收 · 权限校验">
    <template #actions>
      <el-button type="primary" @click="openApply">＋ 申请权限</el-button>
      <el-button @click="refreshAll">刷新</el-button>
    </template>

    <div class="stat-row">
      <StatCard icon="👥" label="角色数" :value="roles.length" :loading="rolesLoading" />
      <StatCard icon="⏳" label="待审批申请" :value="pendingTotal" tone="warning" :loading="apps.loading" clickable @click="activeTab = 'apps'; appQuery.status = 'pending'; reloadApps()" />
      <StatCard icon="🔑" label="有效授权" :value="activeGrantTotal" tone="success" :loading="grants.loading" clickable @click="activeTab = 'grants'" />
      <StatCard icon="🧪" label="校验结果" :value="checkResult ? (checkResult.allowed ? 'ALLOWED' : 'DENIED') : '--'" :tone="checkResult ? (checkResult.allowed ? 'success' : 'danger') : 'primary'" clickable @click="activeTab = 'check'" />
    </div>

    <div class="panel">
      <el-tabs v-model="activeTab">
        <!-- ① 角色管理 -->
        <el-tab-pane label="角色管理" name="roles">
          <div class="toolbar">
            <span class="muted">内置 6 角色照契约 §1.6；自定义角色由系统管理员创建</span>
            <span class="spacer" />
            <el-button v-permission="'user:manage'" type="primary" @click="openRoleDialog()">新建自定义角色</el-button>
          </div>
          <div class="role-grid" v-loading="rolesLoading">
            <div v-for="r in roles" :key="r.code" class="role-card" :class="{ builtin: r.builtin }">
              <div class="role-head">
                <div>
                  <div class="role-name">{{ r.name }}</div>
                  <div class="mono muted">{{ r.code }}</div>
                </div>
                <el-tag size="small" :type="r.builtin ? 'info' : 'warning'" effect="dark">{{ r.builtin ? '内置' : '自定义' }}</el-tag>
              </div>
              <div class="role-scope muted">作用域：{{ r.scope === 'own' ? '仅自有资源' : '全部资源' }}</div>
              <div class="role-perms">
                <el-tag v-for="p in r.permissions || []" :key="p" size="small" effect="plain" class="perm-tag">{{ p }}</el-tag>
                <span v-if="!(r.permissions || []).length" class="muted">无权限</span>
              </div>
              <div class="role-foot">
                <el-button v-permission="'user:manage'" link type="primary" size="small" @click="openRoleDialog(r)">编辑授权</el-button>
              </div>
            </div>
          </div>
        </el-tab-pane>

        <!-- ② 权限矩阵 -->
        <el-tab-pane label="权限矩阵" name="matrix">
          <div class="toolbar">
            <span class="muted">行 = 角色，列 = 资源 × 操作。{{ canManage ? '点击格子即可切换并保存（PUT /roles/{code}）' : '仅系统管理员可编辑' }}</span>
          </div>
          <div class="matrix-wrap" v-loading="matrixLoading">
            <table v-if="matrix" class="matrix">
              <thead>
                <tr>
                  <th rowspan="2" class="sticky">角色</th>
                  <th v-for="res in matrix.resources" :key="res" :colspan="matrix.actions.length" class="res-head">{{ res }}</th>
                </tr>
                <tr>
                  <template v-for="res in matrix.resources" :key="res">
                    <th v-for="act in matrix.actions" :key="res + act" class="act-head">{{ act }}</th>
                  </template>
                </tr>
              </thead>
              <tbody>
                <tr v-for="role in matrix.roles" :key="role.code">
                  <td class="sticky role-cell"><b>{{ role.name }}</b><div class="mono muted">{{ role.code }}</div></td>
                  <template v-for="res in matrix.resources" :key="res">
                    <td v-for="act in matrix.actions" :key="res + act" class="cell" :class="{ on: hasGrant(role, res, act), editable: canManage, saving: savingCell === `${role.code}:${res}:${act}` }" @click="toggleCell(role, res, act)">
                      <span v-if="hasGrant(role, res, act)">✔</span>
                    </td>
                  </template>
                </tr>
              </tbody>
            </table>
          </div>
        </el-tab-pane>

        <!-- ③ 申请列表 -->
        <el-tab-pane label="申请审批" name="apps">
          <div class="toolbar">
            <el-radio-group v-model="appQuery.status" @change="reloadApps">
              <el-radio-button value="">全部</el-radio-button>
              <el-radio-button value="pending">pending</el-radio-button>
              <el-radio-button value="approved">approved</el-radio-button>
              <el-radio-button value="rejected">rejected</el-radio-button>
              <el-radio-button value="expired">expired</el-radio-button>
            </el-radio-group>
            <el-button @click="loadApps">查询</el-button>
          </div>
          <el-table :data="apps.items" v-loading="apps.loading" size="small" stripe>
            <el-table-column prop="id" label="ID" width="60" />
            <el-table-column label="申请人" min-width="180">
              <template #default="{ row }">{{ row.applicantName || '--' }}<br /><HashText :value="row.applicantDid" :head="20" /></template>
            </el-table-column>
            <el-table-column label="资源 / 操作" min-width="150">
              <template #default="{ row }"><span class="mono">{{ row.resourceType }}:{{ row.action }}</span><br /><span class="muted">resourceId {{ row.resourceId }}</span></template>
            </el-table-column>
            <el-table-column prop="reason" label="理由" min-width="200" show-overflow-tooltip />
            <el-table-column label="状态" width="100">
              <template #default="{ row }"><el-tag size="small" effect="dark" :type="APP_TAG[row.status]">{{ row.status }}</el-tag></template>
            </el-table-column>
            <el-table-column label="申请时间" width="150">
              <template #default="{ row }">{{ fmtDateTime(row.createdAt) }}</template>
            </el-table-column>
            <el-table-column label="有效期至" width="110">
              <template #default="{ row }">{{ fmtDate(row.expireAt) }}</template>
            </el-table-column>
            <el-table-column label="审批意见" min-width="140">
              <template #default="{ row }"><span v-if="row.reviewComment">{{ row.reviewComment }}<br /><span class="muted">{{ fmtDateTime(row.reviewedAt) }}</span></span><span v-else class="muted">--</span></template>
            </el-table-column>
            <el-table-column label="操作" width="140" fixed="right">
              <template #default="{ row }">
                <template v-if="row.status === 'pending'">
                  <el-button v-permission="'user:manage'" link type="success" size="small" @click="openReview(row, 'approve')">通过</el-button>
                  <el-button v-permission="'user:manage'" link type="danger" size="small" @click="openReview(row, 'reject')">驳回</el-button>
                </template>
                <span v-else class="muted">存证 {{ row.evidenceId }}</span>
              </template>
            </el-table-column>
          </el-table>
          <div class="pager">
            <el-pagination v-model:current-page="appQuery.page" v-model:page-size="appQuery.size" :total="apps.total" layout="total, prev, pager, next" @current-change="loadApps" />
          </div>
        </el-tab-pane>

        <!-- ④ 已授权 -->
        <el-tab-pane label="已授权管理" name="grants">
          <div class="toolbar">
            <el-select v-model="grantQuery.did" placeholder="按被授权 DID 筛选" clearable filterable allow-create default-first-option style="width: 340px" popper-class="center-popper" @change="reloadGrants">
              <el-option v-for="u in userDidOptions" :key="u.did" :label="`${u.label} ${shortDid(u.did)}`" :value="u.did" />
            </el-select>
            <el-select v-model="grantQuery.status" placeholder="状态" clearable style="width: 120px" popper-class="center-popper" @change="reloadGrants">
              <el-option label="active" value="active" />
              <el-option label="revoked" value="revoked" />
            </el-select>
            <el-button @click="reloadGrants">查询</el-button>
          </div>
          <el-table :data="grants.items" v-loading="grants.loading" size="small" stripe>
            <el-table-column prop="id" label="ID" width="60" />
            <el-table-column label="被授权主体" min-width="180">
              <template #default="{ row }">{{ row.granteeName || '--' }}<br /><HashText :value="row.did" :head="20" /></template>
            </el-table-column>
            <el-table-column label="资源 / 操作" min-width="150">
              <template #default="{ row }"><span class="mono">{{ row.resourceType }}:{{ row.action }}</span><br /><span class="muted">resourceId {{ row.resourceId }}</span></template>
            </el-table-column>
            <el-table-column label="授权人" min-width="160">
              <template #default="{ row }"><HashText :value="row.grantedBy" :head="20" /></template>
            </el-table-column>
            <el-table-column label="授权时间" width="150">
              <template #default="{ row }">{{ fmtDateTime(row.grantedAt) }}</template>
            </el-table-column>
            <el-table-column label="到期" width="110">
              <template #default="{ row }">{{ fmtDate(row.expireAt) }}</template>
            </el-table-column>
            <el-table-column label="状态" width="90">
              <template #default="{ row }"><el-tag size="small" effect="dark" :type="row.status === 'active' ? 'success' : 'info'">{{ row.status }}</el-tag></template>
            </el-table-column>
            <el-table-column prop="evidenceId" label="存证" width="100" />
            <el-table-column label="操作" width="90" fixed="right">
              <template #default="{ row }">
                <el-button v-if="row.status === 'active'" v-permission="'user:manage'" link type="danger" size="small" @click="doRevoke(row)">回收</el-button>
              </template>
            </el-table-column>
          </el-table>
          <div class="pager">
            <el-pagination v-model:current-page="grantQuery.page" v-model:page-size="grantQuery.size" :total="grants.total" layout="total, prev, pager, next" @current-change="loadGrants" />
          </div>
        </el-tab-pane>

        <!-- ⑤ 校验测试器 -->
        <el-tab-pane label="权限校验测试器" name="check">
          <div class="grid-2">
            <div>
              <el-form :model="checkForm" label-width="110px">
                <el-form-item label="主体 DID">
                  <el-select v-model="checkForm.did" filterable allow-create default-first-option clearable placeholder="留空 = 当前登录用户" style="width: 100%" popper-class="center-popper">
                    <el-option v-for="u in userDidOptions" :key="u.did" :label="`${u.label} ${shortDid(u.did)}`" :value="u.did" />
                  </el-select>
                </el-form-item>
                <el-form-item label="resourceType">
                  <el-radio-group v-model="checkForm.resourceType">
                    <el-radio-button v-for="r in RESOURCES" :key="r" :value="r">{{ r }}</el-radio-button>
                  </el-radio-group>
                </el-form-item>
                <el-form-item label="resourceId"><el-input v-model="checkForm.resourceId" placeholder="如 1001 / task-9 / *" /></el-form-item>
                <el-form-item label="action">
                  <el-radio-group v-model="checkForm.action">
                    <el-radio-button v-for="a in ACTIONS" :key="a" :value="a">{{ a }}</el-radio-button>
                  </el-radio-group>
                </el-form-item>
                <div class="toolbar">
                  <el-button @click="presetVpp">一键示例：vpp 尝试 dispatch:issue</el-button>
                  <el-button @click="presetAdmin">示例：admin 读取 asset 1001</el-button>
                  <span class="spacer" />
                  <el-button type="primary" :loading="checking" @click="doCheck">校 验</el-button>
                </div>
              </el-form>
            </div>
            <div>
              <div v-if="checkResult" class="check-result" :class="checkResult.allowed ? 'ok' : 'bad'">
                <div class="check-big">{{ checkResult.allowed ? 'ALLOWED' : 'DENIED' }}</div>
                <div class="check-sub">{{ checkResult.allowed ? '✔ 允许访问' : '✘ 拒绝访问（已写入审计日志）' }}</div>
                <el-descriptions :column="1" border size="small" style="margin-top: 12px">
                  <el-descriptions-item label="reason">{{ checkResult.reason || '--' }}</el-descriptions-item>
                  <el-descriptions-item label="matchedRule"><span class="mono">{{ checkResult.matchedRule || 'null' }}</span></el-descriptions-item>
                  <el-descriptions-item label="资源等级 level">{{ checkResult.level || '--' }}</el-descriptions-item>
                  <el-descriptions-item label="请求">{{ lastCheckReq }}</el-descriptions-item>
                </el-descriptions>
              </div>
              <div v-else class="empty-tip">填写主体与资源后点击「校验」，结果来自后端 POST /permissions/check（角色矩阵 + 授权记录）</div>
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>

    <!-- 角色新建 / 编辑 -->
    <el-dialog v-model="roleDialogVisible" :title="roleForm.isEdit ? `编辑角色授权 · ${roleForm.name}` : '新建自定义角色'" width="640px" class="center-dialog" destroy-on-close>
      <el-form :model="roleForm" label-width="80px">
        <el-form-item label="角色编码"><el-input v-model="roleForm.code" :disabled="roleForm.isEdit" placeholder="小写字母与下划线，如 data_analyst" /></el-form-item>
        <el-form-item label="角色名称"><el-input v-model="roleForm.name" /></el-form-item>
        <el-form-item label="授权">
          <div class="grant-editor">
            <div v-for="res in RESOURCES" :key="res" class="grant-row">
              <span class="grant-res mono">{{ res }}</span>
              <el-checkbox-group v-model="roleForm.grants[res]">
                <el-checkbox v-for="a in ACTIONS" :key="a" :value="a" :label="a" />
              </el-checkbox-group>
            </div>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="roleDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="roleSaving" @click="submitRole">保存</el-button>
      </template>
    </el-dialog>

    <!-- 审批 -->
    <el-dialog v-model="reviewVisible" :title="reviewForm.kind === 'approve' ? `审批通过 · 申请 #${reviewForm.id}` : `驳回 · 申请 #${reviewForm.id}`" width="480px" class="center-dialog">
      <el-input v-model="reviewForm.comment" type="textarea" :rows="3" placeholder="审批意见（将写入 perm_change_log 与存证）" />
      <template #footer>
        <el-button @click="reviewVisible = false">取消</el-button>
        <el-button :type="reviewForm.kind === 'approve' ? 'success' : 'danger'" :loading="reviewing" @click="submitReview">{{ reviewForm.kind === 'approve' ? '通过并生成授权' : '确认驳回' }}</el-button>
      </template>
    </el-dialog>

    <!-- 申请权限 -->
    <el-dialog v-model="applyVisible" title="申请权限" width="520px" class="center-dialog">
      <el-form :model="applyForm" label-width="100px">
        <el-form-item label="resourceType">
          <el-radio-group v-model="applyForm.resourceType">
            <el-radio-button v-for="r in RESOURCES" :key="r" :value="r">{{ r }}</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="resourceId"><el-input v-model="applyForm.resourceId" placeholder="资产 ID / 任务 ID / *" /></el-form-item>
        <el-form-item label="action">
          <el-radio-group v-model="applyForm.action">
            <el-radio-button v-for="a in ACTIONS" :key="a" :value="a">{{ a }}</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="理由"><el-input v-model="applyForm.reason" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="有效期至">
          <el-date-picker v-model="applyForm.expireAt" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss+08:00" style="width: 100%" popper-class="center-popper" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="applyVisible = false">取消</el-button>
        <el-button type="primary" :loading="applying" @click="submitApply">提交</el-button>
      </template>
    </el-dialog>
  </CenterPage>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listRoles, createRole, updateRole, getPermissionMatrix, applyPermission, listApplications, approveApplication, rejectApplication,
  listGrants, revokeGrant, checkPermission, listUsers, listDids
} from '@/api'
import { useUserStore } from '@/stores/user'
import { useLogStore } from '@/stores/logs'
import { fmtDateTime, fmtDate, shortDid, toIso8, ROLE_LABELS } from '@/utils/format'
import CenterPage from '@/components/center/CenterPage.vue'
import StatCard from '@/components/center/StatCard.vue'
import HashText from '@/components/center/HashText.vue'

const userStore = useUserStore()
const logStore = useLogStore()
const RESOURCES = ['asset', 'model', 'dispatch', 'evidence', 'algo']
const ACTIONS = ['read', 'write', 'execute', 'issue', 'export']
const APP_TAG = { pending: 'warning', approved: 'success', rejected: 'danger', expired: 'info' }
const canManage = computed(() => userStore.hasPermission('user:manage'))
const activeTab = ref('roles')

/* ---------- 角色 ---------- */
const roles = ref([])
const rolesLoading = ref(false)
async function loadRoles() {
  rolesLoading.value = true
  try { roles.value = (await listRoles()).items || [] } catch { /* 拦截器已提示 */ } finally { rolesLoading.value = false }
}
const roleDialogVisible = ref(false)
const roleSaving = ref(false)
const roleForm = reactive({ isEdit: false, code: '', name: '', grants: {} })
function emptyGrants() { return Object.fromEntries(RESOURCES.map(r => [r, []])) }
function openRoleDialog(role) {
  roleForm.isEdit = Boolean(role)
  roleForm.code = role?.code || ''
  roleForm.name = role?.name || ''
  roleForm.grants = emptyGrants()
  if (role?.grants) for (const r of RESOURCES) roleForm.grants[r] = [...(role.grants[r] || [])]
  roleDialogVisible.value = true
}
async function submitRole() {
  if (!roleForm.code || !roleForm.name) { ElMessage.warning('请填写编码与名称'); return }
  roleSaving.value = true
  try {
    const grants = { ...roleForm.grants }
    if (roleForm.isEdit) {
      // 保留 user:manage 等矩阵外的授权
      const old = roles.value.find(r => r.code === roleForm.code)?.grants || {}
      for (const [k, v] of Object.entries(old)) if (!RESOURCES.includes(k)) grants[k] = v
      await updateRole(roleForm.code, { name: roleForm.name, grants })
      logStore.addLog(`修改角色 ${roleForm.code} 授权`, 'WARN', 'PERMISSION')
    } else {
      await createRole({ code: roleForm.code, name: roleForm.name, grants })
      logStore.addLog(`新建自定义角色 ${roleForm.code}（${roleForm.name}）`, 'INFO', 'PERMISSION')
    }
    ElMessage.success('已保存')
    roleDialogVisible.value = false
    loadRoles(); loadMatrix()
  } catch { /* 拦截器已提示 */ } finally { roleSaving.value = false }
}

/* ---------- 矩阵 ---------- */
const matrix = ref(null)
const matrixLoading = ref(false)
const savingCell = ref('')
async function loadMatrix() {
  matrixLoading.value = true
  try { matrix.value = await getPermissionMatrix() } catch { /* 拦截器已提示 */ } finally { matrixLoading.value = false }
}
function hasGrant(role, res, act) { return Boolean(role.grants?.[res]?.includes(act)) }
async function toggleCell(role, res, act) {
  if (!canManage.value || savingCell.value) return
  const key = `${role.code}:${res}:${act}`
  savingCell.value = key
  const grants = JSON.parse(JSON.stringify(role.grants || {}))
  const cur = grants[res] || []
  grants[res] = cur.includes(act) ? cur.filter(a => a !== act) : [...cur, act]
  try {
    await updateRole(role.code, { grants })
    role.grants = grants
    logStore.addLog(`权限矩阵：${role.code} ${grants[res].includes(act) ? '+' : '-'} ${res}:${act}`, 'WARN', 'PERMISSION')
    loadRoles()
  } catch { /* 拦截器已提示 */ } finally { savingCell.value = '' }
}

/* ---------- 申请 ---------- */
const appQuery = reactive({ status: '', page: 1, size: 10 })
const apps = reactive({ items: [], total: 0, loading: false })
const pendingTotal = ref(null)
async function loadApps() {
  apps.loading = true
  try {
    const data = await listApplications({ page: appQuery.page, size: appQuery.size, status: appQuery.status || undefined })
    apps.items = data.items || []; apps.total = data.total || 0
    if (appQuery.status === 'pending') pendingTotal.value = apps.total
  } catch { /* 拦截器已提示 */ } finally { apps.loading = false }
}
function reloadApps() { appQuery.page = 1; loadApps() }
async function loadPendingTotal() {
  try { pendingTotal.value = (await listApplications({ status: 'pending', size: 1 })).total } catch { /* 忽略 */ }
}
const reviewVisible = ref(false)
const reviewing = ref(false)
const reviewForm = reactive({ id: null, kind: 'approve', comment: '' })
function openReview(row, kind) {
  Object.assign(reviewForm, { id: row.id, kind, comment: kind === 'approve' ? '同意，按最小必要原则授权' : '' })
  reviewVisible.value = true
}
async function submitReview() {
  reviewing.value = true
  try {
    if (reviewForm.kind === 'approve') {
      const data = await approveApplication(reviewForm.id, { comment: reviewForm.comment })
      logStore.addLog(`审批通过申请 #${reviewForm.id}，生成授权 #${data.grantId}，存证 ${data.evidenceId}`, 'INFO', 'PERMISSION')
    } else {
      const data = await rejectApplication(reviewForm.id, { comment: reviewForm.comment })
      logStore.addLog(`驳回申请 #${reviewForm.id}：${reviewForm.comment}，存证 ${data.evidenceId}`, 'WARN', 'PERMISSION')
    }
    ElMessage.success('已处理')
    reviewVisible.value = false
    loadApps(); loadPendingTotal(); loadGrants()
  } catch { /* 拦截器已提示 */ } finally { reviewing.value = false }
}
const applyVisible = ref(false)
const applying = ref(false)
const applyForm = reactive({ resourceType: 'asset', resourceId: '1001', action: 'read', reason: '联合建模需要读取节点A光伏数据', expireAt: '' })
function openApply() {
  applyForm.expireAt = toIso8(new Date(Date.now() + 30 * 86400000))
  applyVisible.value = true
}
async function submitApply() {
  applying.value = true
  try {
    const data = await applyPermission({ resourceType: applyForm.resourceType, resourceId: applyForm.resourceId, action: applyForm.action, reason: applyForm.reason, expireAt: applyForm.expireAt || undefined })
    ElMessage.success(`申请已提交 #${data.id}`)
    logStore.addLog(`提交权限申请 #${data.id}：${applyForm.resourceType}:${applyForm.action}（${applyForm.resourceId}），存证 ${data.evidenceId}`, 'INFO', 'PERMISSION')
    applyVisible.value = false
    activeTab.value = 'apps'; appQuery.status = ''; reloadApps(); loadPendingTotal()
  } catch { /* 拦截器已提示 */ } finally { applying.value = false }
}

/* ---------- 授权 ---------- */
const grantQuery = reactive({ did: '', status: '', page: 1, size: 10 })
const grants = reactive({ items: [], total: 0, loading: false })
const activeGrantTotal = ref(null)
async function loadGrants() {
  grants.loading = true
  try {
    const data = await listGrants({ page: grantQuery.page, size: grantQuery.size, did: grantQuery.did || undefined, status: grantQuery.status || undefined })
    grants.items = data.items || []; grants.total = data.total || 0
    if (!grantQuery.did && !grantQuery.status) activeGrantTotal.value = grants.items.filter(g => g.status === 'active').length + Math.max(0, grants.total - grants.items.length)
  } catch { /* 拦截器已提示 */ } finally { grants.loading = false }
}
function reloadGrants() { grantQuery.page = 1; loadGrants() }
async function doRevoke(row) {
  try { await ElMessageBox.confirm(`回收授权 #${row.id}（${row.resourceType}:${row.action} → ${shortDid(row.did)}）？`, '回收授权', { type: 'warning' }) } catch { return }
  try {
    const data = await revokeGrant(row.id)
    logStore.addLog(`回收授权 #${row.id}，存证 ${data.evidenceId}`, 'WARN', 'PERMISSION')
    loadGrants()
  } catch { /* 拦截器已提示 */ }
}

/* ---------- 用户 DID 选项（校验器 / 授权筛选） ---------- */
const userDidOptions = ref([])
async function loadUserDids() {
  try {
    if (canManage.value) {
      const data = await listUsers({ size: 100 })
      userDidOptions.value = (data.items || []).map(u => ({ did: u.did, label: `${u.realName}（${u.username} · ${(u.roles || []).map(r => ROLE_LABELS[r] || r).join('/')}）`, username: u.username }))
    } else {
      const data = await listDids({ subjectType: 'user', size: 100 })
      userDidOptions.value = (data.items || []).map(d => ({ did: d.did, label: d.subjectName, username: d.metadata?.username }))
    }
  } catch { /* 忽略 */ }
}

/* ---------- 校验器 ---------- */
const checkForm = reactive({ did: '', resourceType: 'dispatch', resourceId: 'task-9', action: 'issue' })
const checking = ref(false)
const checkResult = ref(null)
const lastCheckReq = ref('')
function presetVpp() {
  const vpp = userDidOptions.value.find(u => u.username === 'vpp' || /虚拟电厂/.test(u.label))
  Object.assign(checkForm, { did: vpp?.did || '', resourceType: 'dispatch', resourceId: 'task-9', action: 'issue' })
  if (!vpp) ElMessage.info('未找到 vpp 用户 DID，将以当前用户身份校验')
  doCheck()
}
function presetAdmin() {
  const admin = userDidOptions.value.find(u => u.username === 'admin' || /系统管理员/.test(u.label))
  Object.assign(checkForm, { did: admin?.did || '', resourceType: 'asset', resourceId: '1001', action: 'read' })
  doCheck()
}
async function doCheck() {
  checking.value = true
  try {
    const req = { did: checkForm.did || userStore.did, resourceType: checkForm.resourceType, resourceId: checkForm.resourceId, action: checkForm.action }
    lastCheckReq.value = `${shortDid(req.did)} → ${req.resourceType}:${req.action} (${req.resourceId})`
    checkResult.value = await checkPermission(req)
    logStore.addLog(`权限校验 ${lastCheckReq.value} → ${checkResult.value.allowed ? 'ALLOWED' : 'DENIED'}：${checkResult.value.reason}`, checkResult.value.allowed ? 'INFO' : 'WARN', 'PERMISSION')
  } catch { /* 拦截器已提示 */ } finally { checking.value = false }
}

function refreshAll() { loadRoles(); loadMatrix(); loadPendingTotal(); loadGrants(); loadUserDids(); if (activeTab.value === 'apps') loadApps() }
watch(activeTab, tab => { if (tab === 'apps' && !apps.items.length) loadApps() })
onMounted(refreshAll)
</script>

<style scoped>
.role-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; }
.role-card { padding: 14px; border-radius: 10px; border: 1px solid rgba(0, 180, 216, .25); background: rgba(0, 180, 216, .04); display: flex; flex-direction: column; gap: 8px; transition: all .25s; }
.role-card:hover { border-color: var(--color-primary); box-shadow: 0 0 16px rgba(0, 180, 216, .2); }
.role-card:not(.builtin) { border-style: dashed; }
.role-head { display: flex; justify-content: space-between; align-items: flex-start; }
.role-name { font-weight: 600; font-size: 15px; }
.role-scope { font-size: 12px; }
.role-perms { display: flex; flex-wrap: wrap; gap: 4px; min-height: 24px; }
.perm-tag { font-family: Consolas, monospace; }
.role-foot { display: flex; justify-content: flex-end; }
.matrix-wrap { overflow: auto; border: 1px solid rgba(0, 180, 216, .2); border-radius: 8px; }
.matrix { border-collapse: collapse; min-width: 100%; font-size: 12px; }
.matrix th, .matrix td { border: 1px solid rgba(0, 180, 216, .12); text-align: center; padding: 6px 4px; }
.matrix th { background: var(--bg-tertiary); color: var(--color-primary); }
.matrix .res-head { font-size: 13px; letter-spacing: 1px; border-left: 2px solid rgba(0, 180, 216, .4); }
.matrix .act-head { font-weight: 400; color: var(--color-text-secondary); min-width: 52px; }
.matrix .sticky { position: sticky; left: 0; background: var(--bg-tertiary); z-index: 1; text-align: left; padding-left: 10px; min-width: 150px; }
.matrix .role-cell { color: var(--color-text); }
.matrix .cell { height: 40px; color: var(--color-success); font-weight: 700; font-size: 14px; transition: background .2s; }
.matrix .cell.on { background: rgba(46, 204, 113, .18); }
.matrix .cell.editable { cursor: pointer; }
.matrix .cell.editable:hover { background: rgba(0, 180, 216, .25); }
.matrix .cell.saving { animation: pulse .6s infinite; }
.matrix tbody tr:hover td { background-color: rgba(0, 180, 216, .05); }
.grant-editor { display: flex; flex-direction: column; gap: 6px; width: 100%; }
.grant-row { display: flex; align-items: center; gap: 12px; }
.grant-res { width: 80px; color: var(--color-primary); }
.check-result { padding: 18px; border-radius: 10px; border: 1px solid; animation: fadeIn .4s; }
.check-result.ok { border-color: var(--color-success); background: rgba(46, 204, 113, .08); }
.check-result.bad { border-color: var(--color-danger); background: rgba(230, 57, 70, .08); }
.check-big { font-size: 40px; font-weight: 800; letter-spacing: 6px; text-align: center; font-family: Consolas, monospace; }
.check-result.ok .check-big { color: var(--color-success); text-shadow: 0 0 18px rgba(46, 204, 113, .6); }
.check-result.bad .check-big { color: var(--color-danger); text-shadow: 0 0 18px rgba(230, 57, 70, .6); }
.check-sub { text-align: center; color: var(--color-text-secondary); }
</style>
