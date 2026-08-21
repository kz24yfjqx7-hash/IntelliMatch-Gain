# STATUS-frontend-pages

更新：2026-08-21

## 已完成

### 页面（整体覆盖占位文件）
| 文件 | 内容 |
|---|---|
| `frontend/src/views/IdentityCenter.vue` | 顶部统计卡（DID 总数 / active / frozen / revoked / 密钥总数，`listDids` 分状态 total）；Tab ①用户管理（`listUsers` 表格，新建/编辑/删除 `v-permission="'user:manage'"`，非管理员显示提示不请求）②DID 管理（subjectType/status/keyword 筛选、分页；注册 DID 对话框 → 结果弹窗展示 DID/公钥/**私钥仅此一次**/chainTxId/evidenceId/didDocument；行操作：文档查看器（JSON 高亮）、冻结/解冻/注销（填 reason）、密钥轮换、跳验签）③密钥管理（按 did/status 筛选、生成绑定（SM2/ECC/RSA、有效期）、冻结/注销、轮换历史时间轴）④验签演示（DID 下拉、message、signature；「使用刚注册的私钥模拟签名」按钮（注册/轮换/生成密钥后可用，`sig:sha256(privateKey|message)`）、「填入无效签名」；valid/reason + subjectType/status 徽章；「消息→SM3 摘要→SM2 验签→结果」流程动画） |
| `frontend/src/views/AssetsCenter.vue` | 统计卡（total/authorized/onChain + 四级计数）+ ECharts（byLevel 饼图、byType 柱图，点击即筛选）；登记对话框（name/dataType/sourceDid（device+edge 活跃 DID）/freq/volume/payload JSON/description/level 手选；「自动分级」调 `classifyAssets` 回填 level，展示 score/reason/cluster + 因子雷达图（含簇中心））→ `registerAsset` 结果弹窗（hash/chainTxId/evidenceId/authStatus/traceId，可直达溯源/申请授权）；资产列表（dataType/level/sourceDid/keyword 筛选、分页）行操作：详情抽屉（payload JSON）、溯源抽屉（register→authorize→access→compute 四阶段卡片 + ECharts graph，每步 actorDid/evidenceId/hash）、申请授权对话框（action/reason/expireAt → `applyPermission`） |
| `frontend/src/views/PermissionCenter.vue` | 统计卡（角色数 / 待审批 / 有效授权 / 最近校验）；Tab ①角色卡片（新建自定义角色 / 编辑授权，按资源×操作勾选）②权限矩阵（行=角色，列=resource×action 双层表头，sys_admin 点格子即 `updateRole` 保存）③申请审批（status 筛选、分页；pending 行 通过/驳回 `v-permission="'user:manage'"`，填意见）④已授权管理（did/status 筛选、回收）⑤权限校验测试器（did/resourceType/resourceId/action → `checkPermission` → 大号 ALLOWED/DENIED + reason/matchedRule/level；预置「vpp 尝试 dispatch:issue」「admin 读取 asset 1001」一键示例）；顶部「申请权限」通用入口 |
| `frontend/src/views/EvidenceCenter.vue` | 链状态面板（height/lastHash/totalRecords/intact/brokenAt/被篡改数 + byCategory 饼图；`intact=false` 整块标红并显示断裂高度）；最近 16 块横向区块链条（height/类别/hash 前 8 位/evidenceId；被篡改块红色闪烁，断裂点之后黄色「受影响」；WS `evidence_written` 实时追加 + 节流刷新）；存证检索（category/dataType/did/from~to 筛选、分页，篡改行整行标红）行操作：详情抽屉、完整性校验弹窗（intact 绿/红、localHash vs chainHash 对比、tamperedAt）、导出凭证（`getCertificate` → JSON Blob 下载）；**篡改演示**按钮（`hasRole('sys_admin')`；选 data 类存证 + newValue 默认 `{"pvOutput":999.9}` → `tamperEvidence` → 自动 `verifyEvidence` + `getChainStatus` + 刷新列表/链条，addLog ERROR）；业务链路追踪（traceId → `traceEvidence` 时间轴；可从选中存证/详情一键带入 traceId） |
| `frontend/src/components/TrustFlowBanner.vue` | 首页六环节流程横幅，自包含（自拉计数、WS 节流 3s 刷新、点击跳转）。插入方式见 `MSG-frontend-pages-to-frontend-legacy-001.md` |

### 子组件 `frontend/src/components/center/`
`CenterPage.vue`（页面外壳 + 中心页/对话框/下拉的深色皮肤）、`StatCard.vue`、`JsonViewer.vue`（转义后着色）、`HashText.vue`（缩略 + 点击复制）、`EChart.vue`（ECharts 薄封装，无 canvas 环境安全降级）、`lastKey.js`（注册/轮换后私钥会话内共享 + 演示签名）、`chartTheme.js`（配色）。

### 冒烟测试
`frontend/src/views/__smoke__/centers.spec.js`（admin 登录 + MSW + 挂载四页与 Banner，断言无错误且关键标题/数据渲染）：**5/5 通过**（`npx vitest run src/views/__smoke__`）。

## 自测结果
- `npx vitest run src/views/__smoke__`：5 passed。
- `VITE_USE_MOCK=true npx vite --port 5199`：`/identity` 200；`/src/views/{Identity,Assets,Permission,Evidence}Center.vue`、`/src/components/TrustFlowBanner.vue` 均 200（转换无错）；已杀掉。
- 本人文件独立入口 `vite build`（router 打桩以绕开下文问题）：零错误零警告。
- `grep -rn "https\?://" src/views src/components`：无外网 URL。
- **整体 `npm run build` 当前失败**，原因在 frontend-legacy 范围：`src/views/CloudAggregate.vue` 仍 import 已删除的 `src/services/aiReportGenerator`。已在 MSG 中通知。

## 对外约定 / 依赖说明
- 只从 `@/api` 导入；未改 api/mocks/stores/router；未新增 npm 包。
- 用到的 mock 补充字段（契约之外、可选）：`listEvidence.items[].tampered`、`getChainStatus.tamperedIds/algorithm`、`keyHistory.versions/items`、`listDids.items[].keyVersion`、`getDidDocument.keys`、`classifyAssets.results[].factors/cluster` 与 `clusterCenters`。真实后端若不返回，页面以 `--`/空处理，不报错。
- 篡改演示按钮按角色判断 `sys_admin`（契约规定仅 sys_admin 可调用）。
- 验签演示：mock 规则 signature 长度 ≥8 且不以 invalid/bad 开头即通过；页面「模拟签名」生成 `sig:<sha256>`，真实后端由设备 SM2 私钥签名。

## 已知问题 / 待办
- 整体构建被 `CloudAggregate.vue` 的失效 import 阻塞（frontend-legacy）。
- 权限中心「有效授权」统计在分页超过一页时为近似值（`listGrants` 无 status 聚合接口）。
- 页面内 ECharts 在无 canvas 环境（jsdom）降级为空容器，浏览器正常。

## 阻塞
无
