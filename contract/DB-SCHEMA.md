# 数据库契约 v1.0（冻结）

数据库 `energy_tds`，MySQL 8.0，字符集 `utf8mb4` / `utf8mb4_unicode_ci`。

**全部表由甲创建和维护，乙不建表、不直连数据库。** 乙只需知道字段命名，以便理解 API 返回值。

建表脚本位置：`backend/sql/01_schema.sql`（结构）、`backend/sql/02_seed.sql`（种子数据）。
容器启动时由 MySQL 官方镜像的 `/docker-entrypoint-initdb.d` 自动执行——**乙的 docker-compose.yml 必须挂载 `./backend/sql:/docker-entrypoint-initdb.d:ro`**。

---

## 表清单

| 表名 | 用途 | 对应需求 |
|---|---|---|
| `sys_user` | 用户 | 3.1 |
| `sys_role` | 角色 | 3.1 |
| `sys_user_role` | 用户-角色关联 | 3.1 |
| `sys_role_permission` | 角色-资源-操作权限 | 3.1 / 3.5 |
| `did_identity` | DID 身份主表 | 3.2 |
| `did_key` | 密钥表 | 3.3 |
| `did_key_rotation_log` | 密钥轮换日志 | 3.3 |
| `did_device_binding` | 设备身份绑定关系 | 3.2 |
| `energy_asset` | 能源数据资产 | 3.4 |
| `energy_asset_lineage` | 资产溯源记录 | 3.4 |
| `perm_application` | 权限申请 | 3.5 |
| `perm_grant` | 已授权记录 | 3.5 |
| `perm_change_log` | 权限变更留痕 | 3.5 |
| `chain_evidence` | 存证记录（本地哈希链） | 3.6 |
| `audit_log_YYYYMM` | 审计日志（按月分表） | 3.7 |
| `audit_alert` | 风险告警 | 3.7 |
| `algo_fl_task` | 联邦学习任务 | 3.8 |
| `algo_fl_round` | 每轮训练指标 | 3.8 |
| `algo_model_version` | 模型版本 | 3.8 |
| `algo_dispatch_task` | 调度任务 | 3.9 |
| `algo_ai_analysis` | AI 分析记录 | 3.10 |
| `node_info` | 节点信息 | 支撑现有页面 |
| `node_metric` | 节点历史指标 | 支撑现有页面 |

---

## 关键表结构（甲按此实现，字段名不得改）

```sql
CREATE TABLE did_identity (
  id            BIGINT PRIMARY KEY AUTO_INCREMENT,
  did           VARCHAR(128) NOT NULL UNIQUE COMMENT 'did:vpp:<type>:<hash>',
  subject_type  ENUM('user','device','org','edge') NOT NULL,
  subject_name  VARCHAR(128) NOT NULL,
  org_name      VARCHAR(128),
  controller_did VARCHAR(128),
  did_document  JSON NOT NULL,
  status        ENUM('active','frozen','revoked') NOT NULL DEFAULT 'active',
  metadata      JSON,
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_type_status (subject_type, status),
  INDEX idx_subject_name (subject_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE chain_evidence (
  id            BIGINT PRIMARY KEY AUTO_INCREMENT,
  evidence_id   VARCHAR(64) NOT NULL UNIQUE COMMENT 'ev-000457',
  category      ENUM('data','identity','permission','audit','algo') NOT NULL,
  ref_id        VARCHAR(64) NOT NULL COMMENT '业务对象ID',
  actor_did     VARCHAR(128),
  payload_hash  VARCHAR(80) NOT NULL COMMENT 'sm3:xxxx',
  prev_hash     VARCHAR(80) NOT NULL,
  block_hash    VARCHAR(80) NOT NULL COMMENT 'SM3(prev_hash + payload_hash + ts)',
  block_height  BIGINT NOT NULL,
  tx_id         VARCHAR(64) NOT NULL,
  trace_id      VARCHAR(64),
  payload_snapshot JSON COMMENT '仅用于完整性校验重算，链上概念中不存在',
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_height (block_height),
  INDEX idx_category_created (category, created_at),
  INDEX idx_actor (actor_did),
  INDEX idx_trace (trace_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE audit_log_202608 (
  id            BIGINT PRIMARY KEY AUTO_INCREMENT,
  trace_id      VARCHAR(64) NOT NULL,
  actor_did     VARCHAR(128),
  actor_name    VARCHAR(128),
  module        VARCHAR(32) NOT NULL COMMENT 'auth|did|key|asset|permission|evidence|algo|audit',
  action        VARCHAR(64) NOT NULL COMMENT 'dispatch:issue',
  resource_type VARCHAR(32),
  resource_id   VARCHAR(64),
  result        ENUM('success','failed','denied') NOT NULL,
  risk_level    ENUM('low','medium','high','critical') NOT NULL DEFAULT 'low',
  detail        TEXT,
  ip            VARCHAR(64),
  evidence_id   VARCHAR(64),
  hash          VARCHAR(80),
  created_at    DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  INDEX idx_trace (trace_id),
  INDEX idx_risk_time (risk_level, created_at),
  INDEX idx_actor_time (actor_did, created_at),
  INDEX idx_action (action)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

分表规则：表名 `audit_log_` + `YYYYMM`。甲需实现自动建表（写入前检查当月表是否存在）与跨月查询合并（`UNION ALL`）。

---

## 演示账号（种子数据，密码 bcrypt 存储）

| 用户名 | 密码 | 角色 | 姓名 |
|---|---|---|---|
| `admin` | `admin123` | `sys_admin` | 系统管理员 |
| `grid` | `grid123` | `grid_dispatcher` | 电网调度员 |
| `vpp` | `vpp123` | `vpp_operator` | 虚拟电厂运营商 |
| `subject` | `subject123` | `energy_subject` | 能源主体 |
| `regulator` | `reg123` | `regulator` | 监管方 |
| `edge` | `edge123` | `edge_node` | 边缘节点 |

## 角色权限矩阵（种子数据）

`✓` 表示拥有该权限。

| 角色 | asset:read | asset:write | asset:export | model:read | dispatch:read | dispatch:issue | evidence:read | algo:execute | 用户管理 |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| sys_admin | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| grid_dispatcher | ✓ | | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | |
| vpp_operator | ✓ | ✓ | | ✓ | ✓ | | ✓ | | |
| energy_subject | 仅自有 | ✓ | | | | | 仅自有 | | |
| regulator | ✓ | | ✓ | ✓ | ✓ | | ✓ | | |
| edge_node | 仅自有 | ✓ | | ✓ | ✓ | | | | |

> 对应需求文档 B 的约束：DeepSeek 分析接口对所有角色只读，无调度下发权限；
> 联邦聚合与 DQN 调度的 `algo:execute` 仅 `sys_admin` 与 `grid_dispatcher` 可解锁。

## 种子节点（与现有 raspi/public/data/mock.json 对齐）

| id | name | status | model | pvOutput | storageOutput | load | soc |
|---|---|---|---|---|---|---|---|
| Node-A | 虚拟电厂节点A | online | VPP-2000 | 45.3 | -12.0 | 120 | 65 |
| Node-B | 虚拟电厂节点B | online | VPP-2000 | 32.1 | 8.5 | 85 | 78 |
| Node-C | 虚拟电厂节点C | warning | VPP-3000 | 28.7 | -25.3 | 150 | 42 |
| Node-D | 虚拟电厂节点D | online | VPP-2000 | 38.9 | 5.2 | 95 | 82 |

每个节点在 `02_seed.sql` 中预置一个 `edge` 类型 DID 并绑定，状态 `active`。

种子数据还需包含：约 80 条 `energy_asset`（覆盖 5 种 dataType、4 个等级）、
30 天 `node_metric` 历史（供曲线图）、若干条已完成的权限申请与审计日志，
确保前端每个页面**首次打开就有内容**，不出现空表。
