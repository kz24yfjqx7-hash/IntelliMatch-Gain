<template>
  <CenterPage icon="🪪" title="统一身份与可信接入中心" desc="DID 签发 · 文档解析 · 密钥生命周期 · SM2 验签接入">
    <template #actions>
      <el-button type="primary" @click="openRegister">＋ 注册 DID</el-button>
      <el-button @click="refreshAll" :loading="statsLoading">刷新</el-button>
    </template>

    <!-- 顶部统计 -->
    <div class="stat-row">
      <StatCard icon="🪪" label="DID 总数" :value="stats.total" :loading="statsLoading" clickable @click="filterByStatus('')" />
      <StatCard icon="✅" label="active 活跃" :value="stats.active" tone="success" :loading="statsLoading" clickable @click="filterByStatus('active')" />
      <StatCard icon="❄️" label="frozen 冻结" :value="stats.frozen" tone="warning" :loading="statsLoading" clickable @click="filterByStatus('frozen')" />
      <StatCard icon="⛔" label="revoked 注销" :value="stats.revoked" tone="danger" :loading="statsLoading" clickable @click="filterByStatus('revoked')" />
      <StatCard icon="🔑" label="密钥总数" :value="keyTotal" :loading="keyLoading" />
    </div>

    <div class="panel">
      <el-tabs v-model="activeTab">
        <!-- ① 用户管理 -->
        <el-tab-pane label="用户管理" name="users">
          <div v-if="!canManageUsers" class="empty-tip">当前角色无 user:manage 权限，用户管理仅系统管理员可见。</div>
          <template v-else>
            <div class="toolbar">
              <el-input v-model="userQuery.keyword" placeholder="用户名 / 姓名" clearable style="width: 200px" @keyup.enter="loadUsers" />
              <el-select v-model="userQuery.role" placeholder="角色" clearable style="width: 160px" popper-class="center-popper" @change="loadUsers">
                <el-option v-for="(label, code) in ROLE_LABELS" :key="code" :label="label" :value="code" />
              </el-select>
              <el-button @click="loadUsers">查询</el-button>
              <span class="spacer" />
              <el-button v-permission="'user:manage'" type="primary" @click="openUserDialog()">新建用户</el-button>
            </div>
            <el-table :data="users.items" v-loading="users.loading" size="small" stripe>
              <el-table-column prop="id" label="ID" width="60" />
              <el-table-column prop="username" label="用户名" width="110" />
              <el-table-column prop="realName" label="姓名" width="120" />
              <el-table-column label="角色" min-width="160">
                <template #default="{ row }">
                  <el-tag v-for="r in row.roles" :key="r" size="small" effect="dark" class="tag-gap">{{ ROLE_LABELS[r] || r }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="DID" min-width="200">
                <template #default="{ row }"><HashText :value="row.did" :head="22" /></template>
              </el-table-column>
              <el-table-column prop="orgName" label="机构" min-width="120" />
              <el-table-column label="状态" width="80">
                <template #default="{ row }">
                  <el-tag :type="row.status === 'active' ? 'success' : 'danger'" size="small">{{ row.status === 'active' ? '正常' : '禁用' }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="创建时间" width="150">
                <template #default="{ row }">{{ fmtDateTime(row.createdAt) }}</template>
              </el-table-column>
              <el-table-column label="操作" width="140" fixed="right">
                <template #default="{ row }">
                  <el-button v-permission="'user:manage'" link type="primary" size="small" @click="openUserDialog(row)">编辑</el-button>
                  <el-button v-permission="'user:manage'" link type="danger" size="small" @click="removeUser(row)">删除</el-button>
                </template>
              </el-table-column>
            </el-table>
            <div class="pager">
              <el-pagination v-model:current-page="userQuery.page" v-model:page-size="userQuery.size" :total="users.total" layout="total, prev, pager, next" @current-change="loadUsers" />
            </div>
          </template>
        </el-tab-pane>

        <!-- ② DID 管理 -->
        <el-tab-pane label="DID 管理" name="dids">
          <div class="toolbar">
            <el-select v-model="didQuery.subjectType" placeholder="主体类型" clearable style="width: 130px" popper-class="center-popper" @change="reloadDids">
              <el-option v-for="(label, v) in SUBJECT_LABELS" :key="v" :label="label" :value="v" />
            </el-select>
            <el-select v-model="didQuery.status" placeholder="状态" clearable style="width: 120px" popper-class="center-popper" @change="reloadDids">
              <el-option label="active" value="active" />
              <el-option label="frozen" value="frozen" />
              <el-option label="revoked" value="revoked" />
            </el-select>
            <el-input v-model="didQuery.keyword" placeholder="DID / 名称 / 机构" clearable style="width: 220px" @keyup.enter="reloadDids" @clear="reloadDids" />
            <el-button @click="reloadDids">查询</el-button>
          </div>
          <el-table :data="dids.items" v-loading="dids.loading" size="small" stripe>
            <el-table-column label="DID" min-width="250">
              <template #default="{ row }"><HashText :value="row.did" :head="26" /></template>
            </el-table-column>
            <el-table-column prop="subjectName" label="主体名称" min-width="140" />
            <el-table-column label="类型" width="80">
              <template #default="{ row }"><el-tag size="small" effect="plain">{{ row.subjectType }}</el-tag></template>
            </el-table-column>
            <el-table-column prop="orgName" label="所属机构" min-width="120" />
            <el-table-column label="状态" width="90">
              <template #default="{ row }"><el-tag :type="STATUS_TAG[row.status]" size="small" effect="dark">{{ row.status }}</el-tag></template>
            </el-table-column>
            <el-table-column label="密钥版本" width="80">
              <template #default="{ row }">v{{ row.keyVersion || 1 }}</template>
            </el-table-column>
            <el-table-column label="签发时间" width="150">
              <template #default="{ row }">{{ fmtDateTime(row.createdAt) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="260" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" size="small" @click="viewDoc(row)">文档</el-button>
                <el-button v-if="row.status === 'active'" link type="warning" size="small" @click="openStatus(row, 'freeze')">冻结</el-button>
                <el-button v-if="row.status === 'frozen'" link type="success" size="small" @click="openStatus(row, 'unfreeze')">解冻</el-button>
                <el-button v-if="row.status !== 'revoked'" link type="danger" size="small" @click="openStatus(row, 'revoke')">注销</el-button>
                <el-button v-if="row.status === 'active'" link size="small" @click="rotate(row)">轮换密钥</el-button>
                <el-button link size="small" @click="gotoVerify(row.did)">验签</el-button>
              </template>
            </el-table-column>
          </el-table>
          <div class="pager">
            <el-pagination v-model:current-page="didQuery.page" v-model:page-size="didQuery.size" :total="dids.total" layout="total, prev, pager, next" @current-change="loadDids" />
          </div>
        </el-tab-pane>

        <!-- ③ 密钥管理 -->
        <el-tab-pane label="密钥管理" name="keys">
          <div class="toolbar">
            <el-select v-model="keyQuery.did" placeholder="按 DID 筛选" clearable filterable style="width: 360px" popper-class="center-popper" @change="reloadKeys">
              <el-option v-for="d in didOptions" :key="d.did" :label="`${d.subjectName}（${d.subjectType}）${shortDid(d.did)}`" :value="d.did" />
            </el-select>
            <el-select v-model="keyQuery.status" placeholder="状态" clearable style="width: 120px" popper-class="center-popper" @change="reloadKeys">
              <el-option label="active" value="active" />
              <el-option label="frozen" value="frozen" />
              <el-option label="revoked" value="revoked" />
            </el-select>
            <el-button @click="reloadKeys">查询</el-button>
            <span class="spacer" />
            <el-button type="primary" @click="openCreateKey">生成并绑定密钥</el-button>
          </div>
          <el-table :data="keys.items" v-loading="keys.loading" size="small" stripe>
            <el-table-column prop="id" label="ID" width="60" />
            <el-table-column label="DID" min-width="230">
              <template #default="{ row }"><HashText :value="row.did" :head="24" /></template>
            </el-table-column>
            <el-table-column prop="algorithm" label="算法" width="70" />
            <el-table-column label="公钥" min-width="160">
              <template #default="{ row }"><HashText :value="row.publicKey" /></template>
            </el-table-column>
            <el-table-column label="版本" width="60">
              <template #default="{ row }">v{{ row.version }}</template>
            </el-table-column>
            <el-table-column label="状态" width="90">
              <template #default="{ row }"><el-tag :type="STATUS_TAG[row.status]" size="small" effect="dark">{{ row.status }}</el-tag></template>
            </el-table-column>
            <el-table-column label="绑定时间" width="150">
              <template #default="{ row }">{{ fmtDateTime(row.boundAt) }}</template>
            </el-table-column>
            <el-table-column label="到期" width="110">
              <template #default="{ row }">{{ fmtDate(row.expireAt) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="180" fixed="right">
              <template #default="{ row }">
                <el-button v-if="row.status === 'active'" link type="warning" size="small" @click="doFreezeKey(row)">冻结</el-button>
                <el-button v-if="row.status !== 'revoked'" link type="danger" size="small" @click="doRevokeKey(row)">注销</el-button>
                <el-button link type="primary" size="small" @click="viewHistory(row)">轮换历史</el-button>
              </template>
            </el-table-column>
          </el-table>
          <div class="pager">
            <el-pagination v-model:current-page="keyQuery.page" v-model:page-size="keyQuery.size" :total="keys.total" layout="total, prev, pager, next" @current-change="loadKeys" />
          </div>
        </el-tab-pane>

        <!-- ④ 验签演示 -->
        <el-tab-pane label="验签演示" name="verify">
          <div class="grid-2">
            <div>
              <el-form label-position="top" :model="verifyForm">
                <el-form-item label="DID">
                  <el-select v-model="verifyForm.did" filterable allow-create default-first-option placeholder="选择或输入 DID" style="width: 100%" popper-class="center-popper">
                    <el-option v-for="d in didOptions" :key="d.did" :label="`${d.subjectName}（${d.status}）${shortDid(d.did)}`" :value="d.did" />
                  </el-select>
                </el-form-item>
                <el-form-item label="消息原文 message">
                  <el-input v-model="verifyForm.message" type="textarea" :rows="3" />
                </el-form-item>
                <el-form-item label="签名 signature（SM2 签名 hex）">
                  <el-input v-model="verifyForm.signature" type="textarea" :rows="2" class="mono" placeholder="由设备端私钥对消息签名后得到" />
                </el-form-item>
                <div class="toolbar">
                  <el-tooltip :content="lastKey.privateKey ? `使用 ${lastKey.source} 得到的私钥（${shortDid(lastKey.did)}）` : '先注册 DID / 轮换密钥 / 生成密钥，才有可用的私钥'" placement="top">
                    <span>
                      <el-button :disabled="!lastKey.privateKey" @click="simulateSign">使用刚注册的私钥模拟签名</el-button>
                    </span>
                  </el-tooltip>
                  <el-button @click="verifyForm.signature = 'invalid'">填入无效签名</el-button>
                  <span class="spacer" />
                  <el-button type="primary" :loading="verifying" @click="doVerify">验 签</el-button>
                </div>
              </el-form>
            </div>
            <div>
              <!-- 流程动画：消息 → SM3 摘要 → SM2 验签 → 结果 -->
              <div class="flow">
                <div v-for="(s, i) in FLOW_STEPS" :key="s.key" class="flow-step" :class="{ active: flowStep >= i, current: flowStep === i && verifying, ok: flowStep > 3 && verifyResult?.valid && i === 3, bad: flowStep > 3 && verifyResult && !verifyResult.valid && i === 3 }">
                  <div class="flow-icon">{{ s.icon }}</div>
                  <div class="flow-label">{{ s.label }}</div>
                  <div v-if="i < 3" class="flow-line"><span class="flow-dot" /></div>
                </div>
              </div>
              <div class="digest mono">摘要（SM3）：{{ digest || '--' }}</div>
              <div v-if="verifyResult" class="verify-result" :class="verifyResult.valid ? 'ok' : 'bad'">
                <div class="verify-big">{{ verifyResult.valid ? '✔ 验签通过 valid' : '✘ 验签失败 invalid' }}</div>
                <div class="verify-meta">
                  <el-tag size="small" effect="plain">subjectType: {{ verifyResult.subjectType || '--' }}</el-tag>
                  <el-tag size="small" :type="STATUS_TAG[verifyResult.status] || 'info'" effect="dark">status: {{ verifyResult.status || '--' }}</el-tag>
                </div>
                <div class="verify-reason">{{ verifyResult.reason || '签名与 DID 文档中的 SM2 公钥匹配，主体状态 active，允许接入' }}</div>
              </div>
              <div v-else class="empty-tip">选择 DID、输入消息与签名后点击「验签」</div>
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>

    <!-- 注册 DID 对话框 -->
    <el-dialog v-model="registerVisible" title="注册 DID（签发可信身份）" width="560px" class="center-dialog" destroy-on-close>
      <el-form ref="registerRef" :model="registerForm" :rules="registerRules" label-width="90px">
        <el-form-item label="主体类型" prop="subjectType">
          <el-radio-group v-model="registerForm.subjectType">
            <el-radio-button v-for="(label, v) in SUBJECT_LABELS" :key="v" :value="v">{{ label }}</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="主体名称" prop="subjectName">
          <el-input v-model="registerForm.subjectName" placeholder="如：光伏逆变器-A01" />
        </el-form-item>
        <el-form-item label="所属机构" prop="orgName">
          <el-input v-model="registerForm.orgName" placeholder="如：XX 园区" />
        </el-form-item>
        <el-form-item label="元数据">
          <el-input v-model="registerForm.metadataText" type="textarea" :rows="3" class="mono" placeholder='{"model":"VPP-2000","location":"A区"}' />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="registerVisible = false">取消</el-button>
        <el-button type="primary" :loading="registering" @click="submitRegister">签发并上链</el-button>
      </template>
    </el-dialog>

    <!-- 签发 / 轮换结果（私钥仅此一次） -->
    <el-dialog v-model="resultVisible" :title="resultTitle" width="640px" class="center-dialog">
      <template v-if="result">
        <el-alert type="warning" :closable="false" show-icon title="私钥仅此一次展示，平台不保存明文私钥，请立即妥善保管到设备侧。" />
        <div class="muted" style="margin-top: 10px">DID</div>
        <div class="key-box">{{ result.did }}</div>
        <div class="muted">公钥 publicKey</div>
        <div class="key-box">{{ result.publicKey }}</div>
        <div class="muted">私钥 privateKey（仅此一次）</div>
        <div class="key-box secret">{{ result.privateKey }}</div>
        <el-descriptions :column="2" border size="small" style="margin-top: 10px">
          <el-descriptions-item label="链上交易">{{ result.chainTxId || '--' }}</el-descriptions-item>
          <el-descriptions-item label="存证 ID">{{ result.evidenceId || '--' }}</el-descriptions-item>
          <el-descriptions-item v-if="result.version" label="密钥版本">v{{ result.version }}</el-descriptions-item>
          <el-descriptions-item v-if="result.createdAt" label="签发时间">{{ fmtDateTime(result.createdAt) }}</el-descriptions-item>
        </el-descriptions>
        <el-collapse v-if="result.didDocument" style="margin-top: 10px">
          <el-collapse-item title="DID 文档 didDocument" name="doc">
            <JsonViewer :value="result.didDocument" title="didDocument" max-height="260px" />
          </el-collapse-item>
        </el-collapse>
      </template>
      <template #footer>
        <el-button @click="resultVisible = false">关闭</el-button>
        <el-button type="primary" @click="resultVisible = false; gotoVerify(result?.did)">去验签演示 →</el-button>
      </template>
    </el-dialog>

    <!-- DID 文档查看 -->
    <el-drawer v-model="docVisible" :title="`DID 文档 · ${docData?.subjectName || ''}`" size="560px" class="center-dialog">
      <template v-if="docData">
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="DID"><span class="mono">{{ docData.did }}</span></el-descriptions-item>
          <el-descriptions-item label="类型 / 状态">{{ docData.subjectType }} / <el-tag size="small" :type="STATUS_TAG[docData.status]" effect="dark">{{ docData.status }}</el-tag></el-descriptions-item>
          <el-descriptions-item label="控制者"><span class="mono">{{ docData.controllerDid || docData.didDocument?.controller }}</span></el-descriptions-item>
          <el-descriptions-item label="绑定密钥">{{ (docData.keys || []).length }} 把（活跃 {{ (docData.keys || []).filter(k => k.status === 'active').length }}）</el-descriptions-item>
        </el-descriptions>
        <div style="margin-top: 12px">
          <JsonViewer :value="docData.didDocument" title="didDocument（W3C DID Core）" />
        </div>
      </template>
    </el-drawer>

    <!-- 状态变更 -->
    <el-dialog v-model="statusVisible" :title="`${STATUS_ACTION_LABELS[statusForm.action]} DID`" width="480px" class="center-dialog">
      <p class="mono muted" style="margin-bottom: 10px; word-break: break-all">{{ statusForm.did }}</p>
      <el-input v-model="statusForm.reason" type="textarea" :rows="3" placeholder="原因（将写入存证）" />
      <template #footer>
        <el-button @click="statusVisible = false">取消</el-button>
        <el-button :type="statusForm.action === 'unfreeze' ? 'success' : 'danger'" :loading="statusSubmitting" @click="submitStatus">确认{{ STATUS_ACTION_LABELS[statusForm.action] }}</el-button>
      </template>
    </el-dialog>

    <!-- 生成密钥 -->
    <el-dialog v-model="createKeyVisible" title="生成并绑定密钥" width="520px" class="center-dialog">
      <el-form :model="createKeyForm" label-width="90px">
        <el-form-item label="DID">
          <el-select v-model="createKeyForm.did" filterable style="width: 100%" popper-class="center-popper">
            <el-option v-for="d in didOptions.filter(x => x.status === 'active')" :key="d.did" :label="`${d.subjectName}（${d.subjectType}）${shortDid(d.did)}`" :value="d.did" />
          </el-select>
        </el-form-item>
        <el-form-item label="算法">
          <el-radio-group v-model="createKeyForm.algorithm">
            <el-radio-button value="SM2">SM2</el-radio-button>
            <el-radio-button value="ECC">ECC</el-radio-button>
            <el-radio-button value="RSA">RSA</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="有效期(天)">
          <el-input-number v-model="createKeyForm.validDays" :min="1" :max="3650" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createKeyVisible = false">取消</el-button>
        <el-button type="primary" :loading="creatingKey" @click="submitCreateKey">生成</el-button>
      </template>
    </el-dialog>

    <!-- 轮换历史 -->
    <el-drawer v-model="historyVisible" title="密钥轮换历史" size="480px" class="center-dialog">
      <template v-if="history">
        <p class="mono muted" style="word-break: break-all; margin-bottom: 12px">{{ history.did }}</p>
        <el-timeline>
          <el-timeline-item v-for="v in history.versions || []" :key="v.id" :timestamp="fmtDateTime(v.boundAt)" :type="v.status === 'active' ? 'success' : v.status === 'frozen' ? 'warning' : 'info'" placement="top">
            <div><b>v{{ v.version }}</b> · {{ v.algorithm }} · <el-tag size="small" :type="STATUS_TAG[v.status]" effect="dark">{{ v.status }}</el-tag></div>
            <div class="mono muted">公钥 {{ shortHash(v.publicKey, 16, 8) }}</div>
            <div v-for="r in (history.items || []).filter(x => x.toVersion === v.version)" :key="r.id" class="muted" style="margin-top: 4px">
              ↻ 由 v{{ r.fromVersion }} 轮换而来 · {{ r.reason }} · 操作者 {{ shortDid(r.operator) }}
            </div>
          </el-timeline-item>
        </el-timeline>
        <div v-if="!(history.versions || []).length" class="empty-tip">暂无记录</div>
      </template>
    </el-drawer>

    <!-- 用户新建 / 编辑 -->
    <el-dialog v-model="userDialogVisible" :title="userForm.id ? '编辑用户' : '新建用户'" width="520px" class="center-dialog" destroy-on-close>
      <el-form :model="userForm" label-width="80px">
        <el-form-item label="用户名"><el-input v-model="userForm.username" :disabled="Boolean(userForm.id)" /></el-form-item>
        <el-form-item :label="userForm.id ? '新密码' : '密码'"><el-input v-model="userForm.password" type="password" show-password :placeholder="userForm.id ? '留空则不修改' : ''" /></el-form-item>
        <el-form-item label="姓名"><el-input v-model="userForm.realName" /></el-form-item>
        <el-form-item label="机构"><el-input v-model="userForm.orgName" /></el-form-item>
        <el-form-item label="角色">
          <el-select v-model="userForm.roles" multiple style="width: 100%" popper-class="center-popper">
            <el-option v-for="(label, code) in ROLE_LABELS" :key="code" :label="label" :value="code" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="userForm.id" label="状态">
          <el-radio-group v-model="userForm.status">
            <el-radio value="active">正常</el-radio>
            <el-radio value="disabled">禁用</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="userDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="userSubmitting" @click="submitUser">保存</el-button>
      </template>
    </el-dialog>
  </CenterPage>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listUsers, createUser, updateUser, deleteUser,
  registerDid, listDids, getDidDocument, changeDidStatus, rotateDidKey, verifyDid,
  listKeys, createKey, freezeKey, revokeKey, keyHistory
} from '@/api'
import { useUserStore } from '@/stores/user'
import { useLogStore } from '@/stores/logs'
import { ROLE_LABELS, fmtDateTime, fmtDate, shortDid, shortHash } from '@/utils/format'
import { sha256Hex } from '@/utils/sha256'
import CenterPage from '@/components/center/CenterPage.vue'
import StatCard from '@/components/center/StatCard.vue'
import HashText from '@/components/center/HashText.vue'
import JsonViewer from '@/components/center/JsonViewer.vue'
import { lastKey, setLastKey, demoSign } from '@/components/center/lastKey.js'

const route = useRoute()
const userStore = useUserStore()
const logStore = useLogStore()

const SUBJECT_LABELS = { user: '用户 user', device: '设备 device', org: '机构 org', edge: '边缘 edge' }
const STATUS_TAG = { active: 'success', frozen: 'warning', revoked: 'danger' }
const STATUS_ACTION_LABELS = { freeze: '冻结', unfreeze: '解冻', revoke: '注销' }
const FLOW_STEPS = [
  { key: 'msg', icon: '📨', label: '消息原文' },
  { key: 'sm3', icon: '#', label: 'SM3 摘要' },
  { key: 'sm2', icon: '🔐', label: 'SM2 验签' },
  { key: 'res', icon: '🏁', label: '结果' }
]

const canManageUsers = computed(() => userStore.hasPermission('user:manage'))
const activeTab = ref(route.query.tab === 'verify' ? 'verify' : 'dids')

/* ---------- 统计 ---------- */
const stats = reactive({ total: null, active: null, frozen: null, revoked: null })
const statsLoading = ref(false)
async function loadStats() {
  statsLoading.value = true
  try {
    const [all, active, frozen, revoked] = await Promise.all([
      listDids({ size: 1 }), listDids({ status: 'active', size: 1 }), listDids({ status: 'frozen', size: 1 }), listDids({ status: 'revoked', size: 1 })
    ])
    stats.total = all.total; stats.active = active.total; stats.frozen = frozen.total; stats.revoked = revoked.total
  } catch { /* 拦截器已提示 */ } finally { statsLoading.value = false }
}

/* ---------- DID 下拉选项（密钥 / 验签共用） ---------- */
const didOptions = ref([])
async function loadDidOptions() {
  try {
    const data = await listDids({ size: 200 })
    didOptions.value = data.items || []
  } catch { /* 忽略 */ }
}

/* ---------- 用户管理 ---------- */
const userQuery = reactive({ keyword: '', role: '', page: 1, size: 10 })
const users = reactive({ items: [], total: 0, loading: false })
async function loadUsers() {
  if (!canManageUsers.value) return
  users.loading = true
  try {
    const data = await listUsers({ ...userQuery, keyword: userQuery.keyword || undefined, role: userQuery.role || undefined })
    users.items = data.items || []; users.total = data.total || 0
  } catch { /* 拦截器已提示 */ } finally { users.loading = false }
}
const userDialogVisible = ref(false)
const userSubmitting = ref(false)
const userForm = reactive({ id: null, username: '', password: '', realName: '', orgName: '', roles: ['energy_subject'], status: 'active' })
function openUserDialog(row) {
  Object.assign(userForm, row
    ? { id: row.id, username: row.username, password: '', realName: row.realName, orgName: row.orgName, roles: [...(row.roles || [])], status: row.status || 'active' }
    : { id: null, username: '', password: '', realName: '', orgName: '', roles: ['energy_subject'], status: 'active' })
  userDialogVisible.value = true
}
async function submitUser() {
  userSubmitting.value = true
  try {
    if (userForm.id) {
      const data = { realName: userForm.realName, orgName: userForm.orgName, roles: userForm.roles, status: userForm.status }
      if (userForm.password) data.password = userForm.password
      await updateUser(userForm.id, data)
      logStore.addLog(`修改用户 ${userForm.username}（角色 ${userForm.roles.join(',')}）`, 'INFO', 'IDENTITY')
    } else {
      const u = await createUser({ username: userForm.username, password: userForm.password, realName: userForm.realName, orgName: userForm.orgName, roles: userForm.roles })
      logStore.addLog(`新建用户 ${u.username}，自动签发 DID ${shortDid(u.did)}`, 'INFO', 'IDENTITY')
      loadStats()
    }
    ElMessage.success('已保存')
    userDialogVisible.value = false
    loadUsers()
  } catch { /* 拦截器已提示 */ } finally { userSubmitting.value = false }
}
async function removeUser(row) {
  try {
    await ElMessageBox.confirm(`确认删除用户 ${row.username}？`, '删除确认', { type: 'warning' })
  } catch { return }
  try {
    await deleteUser(row.id)
    logStore.addLog(`删除用户 ${row.username}`, 'WARN', 'IDENTITY')
    loadUsers()
  } catch { /* 拦截器已提示 */ }
}

/* ---------- DID 管理 ---------- */
const didQuery = reactive({ subjectType: '', status: '', keyword: '', page: 1, size: 10 })
const dids = reactive({ items: [], total: 0, loading: false })
async function loadDids() {
  dids.loading = true
  try {
    const data = await listDids({ page: didQuery.page, size: didQuery.size, subjectType: didQuery.subjectType || undefined, status: didQuery.status || undefined, keyword: didQuery.keyword || undefined })
    dids.items = data.items || []; dids.total = data.total || 0
  } catch { /* 拦截器已提示 */ } finally { dids.loading = false }
}
function reloadDids() { didQuery.page = 1; loadDids() }
function filterByStatus(status) { didQuery.status = status; activeTab.value = 'dids'; reloadDids() }

const registerVisible = ref(false)
const registering = ref(false)
const registerRef = ref()
const registerForm = reactive({ subjectType: 'device', subjectName: '', orgName: '', metadataText: '{"model":"VPP-2000","location":"A区"}' })
const registerRules = {
  subjectName: [{ required: true, message: '请输入主体名称', trigger: 'blur' }],
  orgName: [{ required: true, message: '请输入所属机构', trigger: 'blur' }]
}
function openRegister() {
  registerForm.subjectName = ''
  registerForm.orgName = userStore.user?.orgName || ''
  registerVisible.value = true
}
const resultVisible = ref(false)
const resultTitle = ref('')
const result = ref(null)
async function submitRegister() {
  try { await registerRef.value?.validate() } catch { return }
  let metadata = {}
  if (registerForm.metadataText.trim()) {
    try { metadata = JSON.parse(registerForm.metadataText) } catch { ElMessage.warning('元数据不是合法 JSON'); return }
  }
  registering.value = true
  try {
    const data = await registerDid({ subjectType: registerForm.subjectType, subjectName: registerForm.subjectName, orgName: registerForm.orgName, metadata })
    result.value = data
    resultTitle.value = 'DID 签发成功'
    resultVisible.value = true
    registerVisible.value = false
    setLastKey({ did: data.did, privateKey: data.privateKey, publicKey: data.publicKey, source: 'DID 注册' })
    logStore.addLog(`签发 ${registerForm.subjectType} DID ${shortDid(data.did)}（${registerForm.subjectName}），存证 ${data.evidenceId}`, 'INFO', 'IDENTITY')
    reloadDids(); loadStats(); loadDidOptions()
  } catch { /* 拦截器已提示 */ } finally { registering.value = false }
}

const docVisible = ref(false)
const docData = ref(null)
async function viewDoc(row) {
  try {
    docData.value = await getDidDocument(row.did)
    docVisible.value = true
  } catch { /* 拦截器已提示 */ }
}

const statusVisible = ref(false)
const statusSubmitting = ref(false)
const statusForm = reactive({ did: '', action: 'freeze', reason: '' })
function openStatus(row, action) {
  Object.assign(statusForm, { did: row.did, action, reason: action === 'freeze' ? '设备离线超 24 小时' : '' })
  statusVisible.value = true
}
async function submitStatus() {
  statusSubmitting.value = true
  try {
    const data = await changeDidStatus(statusForm.did, { action: statusForm.action, reason: statusForm.reason })
    logStore.addLog(`DID ${shortDid(statusForm.did)} ${STATUS_ACTION_LABELS[statusForm.action]} → ${data.status}，存证 ${data.evidenceId}`, statusForm.action === 'unfreeze' ? 'INFO' : 'WARN', 'IDENTITY')
    ElMessage.success(`已${STATUS_ACTION_LABELS[statusForm.action]}`)
    statusVisible.value = false
    loadDids(); loadStats(); loadDidOptions(); if (activeTab.value === 'keys') loadKeys()
  } catch { /* 拦截器已提示 */ } finally { statusSubmitting.value = false }
}

async function rotate(row) {
  try {
    await ElMessageBox.confirm(`对 ${row.subjectName} 执行密钥轮换？旧密钥将被注销，新私钥仅展示一次。`, '密钥轮换', { type: 'warning' })
  } catch { return }
  try {
    const data = await rotateDidKey(row.did)
    result.value = { ...data, chainTxId: data.chainTxId || data.txId }
    resultTitle.value = `密钥轮换成功 → v${data.version}`
    resultVisible.value = true
    setLastKey({ did: data.did, privateKey: data.privateKey, publicKey: data.publicKey, source: '密钥轮换' })
    logStore.addLog(`DID ${shortDid(row.did)} 密钥轮换至 v${data.version}，存证 ${data.evidenceId}`, 'INFO', 'IDENTITY')
    loadDids(); loadKeys()
  } catch { /* 拦截器已提示 */ }
}

/* ---------- 密钥管理 ---------- */
const keyQuery = reactive({ did: '', status: '', page: 1, size: 10 })
const keys = reactive({ items: [], total: 0, loading: false })
const keyTotal = ref(null)
const keyLoading = ref(false)
async function loadKeys() {
  keys.loading = true
  try {
    const data = await listKeys({ page: keyQuery.page, size: keyQuery.size, did: keyQuery.did || undefined, status: keyQuery.status || undefined })
    keys.items = data.items || []; keys.total = data.total || 0
    if (!keyQuery.did && !keyQuery.status) keyTotal.value = keys.total
  } catch { /* 拦截器已提示 */ } finally { keys.loading = false }
}
function reloadKeys() { keyQuery.page = 1; loadKeys() }
async function loadKeyTotal() {
  keyLoading.value = true
  try { keyTotal.value = (await listKeys({ size: 1 })).total } catch { /* 忽略 */ } finally { keyLoading.value = false }
}
const createKeyVisible = ref(false)
const creatingKey = ref(false)
const createKeyForm = reactive({ did: '', algorithm: 'SM2', validDays: 365 })
function openCreateKey() {
  createKeyForm.did = keyQuery.did || lastKey.did || didOptions.value.find(d => d.status === 'active')?.did || ''
  createKeyVisible.value = true
}
async function submitCreateKey() {
  if (!createKeyForm.did) { ElMessage.warning('请选择 DID'); return }
  creatingKey.value = true
  try {
    const data = await createKey({ did: createKeyForm.did, algorithm: createKeyForm.algorithm, validDays: createKeyForm.validDays })
    result.value = { did: data.did, publicKey: data.publicKey, privateKey: data.privateKey, evidenceId: data.evidenceId, version: data.version, createdAt: data.boundAt }
    resultTitle.value = `密钥生成成功（${data.algorithm} v${data.version}）`
    resultVisible.value = true
    createKeyVisible.value = false
    setLastKey({ did: data.did, privateKey: data.privateKey, publicKey: data.publicKey, source: '密钥生成' })
    logStore.addLog(`为 ${shortDid(data.did)} 生成并绑定 ${data.algorithm} 密钥 v${data.version}`, 'INFO', 'IDENTITY')
    loadKeys(); loadKeyTotal()
  } catch { /* 拦截器已提示 */ } finally { creatingKey.value = false }
}
async function doFreezeKey(row) {
  try {
    await freezeKey(row.id)
    logStore.addLog(`冻结密钥 #${row.id}（${shortDid(row.did)}）`, 'WARN', 'IDENTITY')
    loadKeys()
  } catch { /* 拦截器已提示 */ }
}
async function doRevokeKey(row) {
  try { await ElMessageBox.confirm(`确认注销密钥 #${row.id}？不可恢复。`, '注销密钥', { type: 'warning' }) } catch { return }
  try {
    await revokeKey(row.id)
    logStore.addLog(`注销密钥 #${row.id}（${shortDid(row.did)}）`, 'WARN', 'IDENTITY')
    loadKeys()
  } catch { /* 拦截器已提示 */ }
}
const historyVisible = ref(false)
const history = ref(null)
async function viewHistory(row) {
  try {
    history.value = await keyHistory(row.id)
    historyVisible.value = true
  } catch { /* 拦截器已提示 */ }
}

/* ---------- 验签演示 ---------- */
const verifyForm = reactive({ did: '', message: `能源可信数据空间 接入请求 ${new Date().toISOString().slice(0, 19)}`, signature: '' })
const verifying = ref(false)
const verifyResult = ref(null)
const flowStep = ref(-1)
const digest = computed(() => verifyForm.message ? 'sm3:' + sha256Hex(verifyForm.message).slice(0, 40) + '…' : '')
function gotoVerify(did) {
  if (did) verifyForm.did = did
  activeTab.value = 'verify'
}
function simulateSign() {
  if (!lastKey.privateKey) return
  if (!verifyForm.did) verifyForm.did = lastKey.did
  verifyForm.signature = demoSign(lastKey.privateKey, verifyForm.message)
  ElMessage.success('已用设备私钥对消息签名（演示）')
}
const sleep = ms => new Promise(r => setTimeout(r, ms))
async function doVerify() {
  if (!verifyForm.did) { ElMessage.warning('请选择 DID'); return }
  verifying.value = true
  verifyResult.value = null
  flowStep.value = 0
  try {
    const req = verifyDid({ did: verifyForm.did, message: verifyForm.message, signature: verifyForm.signature })
    await sleep(350); flowStep.value = 1
    await sleep(350); flowStep.value = 2
    const data = await req
    await sleep(300); flowStep.value = 3
    await sleep(200); flowStep.value = 4
    verifyResult.value = data
    logStore.addLog(`DID ${shortDid(verifyForm.did)} 验签${data.valid ? '通过' : '失败'}${data.reason ? '：' + data.reason : ''}`, data.valid ? 'INFO' : 'ERROR', 'IDENTITY')
  } catch (err) {
    flowStep.value = 4
    verifyResult.value = { valid: false, reason: err?.message || '请求失败', subjectType: null, status: null }
  } finally { verifying.value = false }
}

/* ---------- 初始化 ---------- */
function refreshAll() {
  loadStats(); loadDids(); loadDidOptions(); loadKeyTotal()
  if (activeTab.value === 'users') loadUsers()
  if (activeTab.value === 'keys') loadKeys()
}
watch(activeTab, tab => {
  if (tab === 'users' && !users.items.length) loadUsers()
  if (tab === 'keys' && !keys.items.length) loadKeys()
})
onMounted(() => {
  refreshAll()
  if (route.query.did) verifyForm.did = String(route.query.did)
})
</script>

<style scoped>
.tag-gap { margin-right: 4px; }
.flow { display: flex; align-items: flex-start; justify-content: space-between; padding: 14px 6px 6px; }
.flow-step { position: relative; flex: 1; display: flex; flex-direction: column; align-items: center; gap: 6px; opacity: .45; transition: all .3s; }
.flow-step.active { opacity: 1; }
.flow-icon {
  width: 46px; height: 46px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 20px; font-weight: 700;
  background: rgba(0, 180, 216, .12); border: 1px solid rgba(0, 180, 216, .4); color: var(--color-primary); transition: all .3s;
}
.flow-step.current .flow-icon { animation: glow 1s infinite; }
.flow-step.ok .flow-icon { background: rgba(46, 204, 113, .2); border-color: var(--color-success); color: var(--color-success); box-shadow: 0 0 18px rgba(46, 204, 113, .6); }
.flow-step.bad .flow-icon { background: rgba(230, 57, 70, .2); border-color: var(--color-danger); color: var(--color-danger); box-shadow: 0 0 18px rgba(230, 57, 70, .6); }
.flow-label { font-size: 12px; color: var(--color-text-secondary); }
.flow-line { position: absolute; top: 22px; left: calc(50% + 26px); width: calc(100% - 52px); height: 2px; background: rgba(0, 180, 216, .25); overflow: hidden; }
.flow-step.active .flow-line { background: rgba(0, 180, 216, .6); }
.flow-dot { position: absolute; top: -2px; left: -10px; width: 10px; height: 6px; border-radius: 3px; background: #fff; box-shadow: 0 0 8px var(--color-primary); opacity: 0; }
.flow-step.active .flow-dot { opacity: 1; animation: flowDot 1.2s linear infinite; }
@keyframes flowDot { from { left: -10px; } to { left: 100%; } }
.digest { margin: 6px 0 12px; padding: 8px 10px; border-radius: 6px; background: rgba(0, 0, 0, .35); color: var(--color-text-secondary); word-break: break-all; }
.verify-result { padding: 18px; border-radius: 10px; border: 1px solid; text-align: center; animation: fadeIn .4s; }
.verify-result.ok { border-color: var(--color-success); background: rgba(46, 204, 113, .08); }
.verify-result.bad { border-color: var(--color-danger); background: rgba(230, 57, 70, .08); }
.verify-big { font-size: 26px; font-weight: 700; letter-spacing: 2px; }
.verify-result.ok .verify-big { color: var(--color-success); text-shadow: 0 0 14px rgba(46, 204, 113, .6); }
.verify-result.bad .verify-big { color: var(--color-danger); text-shadow: 0 0 14px rgba(230, 57, 70, .6); }
.verify-meta { display: flex; justify-content: center; gap: 8px; margin: 10px 0; }
.verify-reason { font-size: 13px; color: var(--color-text-secondary); }
</style>
