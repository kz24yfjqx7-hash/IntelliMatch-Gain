-- ============================================================================
-- 能源可信数据空间平台 · 建表脚本
-- 依据：contract/DB-SCHEMA.md（冻结版）
-- 执行方式：由 MySQL 官方镜像的 /docker-entrypoint-initdb.d 在容器首次启动时自动执行
-- 约束：纯 SQL，不依赖任何 Python / Shell 逻辑；文件名数字前缀保证执行顺序
-- ============================================================================

CREATE DATABASE IF NOT EXISTS energy_tds
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE energy_tds;

SET NAMES utf8mb4;

-- ----------------------------------------------------------------------------
-- 一、用户与角色（需求 3.1）
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS sys_user (
  id            BIGINT PRIMARY KEY AUTO_INCREMENT,
  username      VARCHAR(64)  NOT NULL UNIQUE,
  password_hash VARCHAR(128) NOT NULL COMMENT 'bcrypt',
  real_name     VARCHAR(64)  NOT NULL,
  org_name      VARCHAR(128) COMMENT '所属机构',
  did           VARCHAR(128) COMMENT '绑定的用户 DID',
  phone         VARCHAR(32),
  email         VARCHAR(128),
  status        ENUM('active','disabled') NOT NULL DEFAULT 'active',
  last_login_at DATETIME,
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_did (did),
  INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='系统用户';

CREATE TABLE IF NOT EXISTS sys_role (
  id          BIGINT PRIMARY KEY AUTO_INCREMENT,
  code        VARCHAR(32)  NOT NULL UNIQUE COMMENT 'sys_admin|grid_dispatcher|vpp_operator|energy_subject|regulator|edge_node',
  name        VARCHAR(64)  NOT NULL,
  description VARCHAR(255),
  is_builtin  TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '内置角色不可删除',
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='角色';

CREATE TABLE IF NOT EXISTS sys_user_role (
  id         BIGINT PRIMARY KEY AUTO_INCREMENT,
  user_id    BIGINT      NOT NULL,
  role_code  VARCHAR(32) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_user_role (user_id, role_code),
  INDEX idx_role (role_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户-角色关联';

-- RBAC 四层模型：主体(角色) - 资源 - 操作 - 数据范围
CREATE TABLE IF NOT EXISTS sys_role_permission (
  id            BIGINT PRIMARY KEY AUTO_INCREMENT,
  role_code     VARCHAR(32) NOT NULL,
  resource_type ENUM('asset','model','dispatch','evidence','algo','user') NOT NULL,
  action        ENUM('read','write','execute','issue','export','manage') NOT NULL,
  scope         ENUM('all','own') NOT NULL DEFAULT 'all' COMMENT 'own = 仅自有数据，对应契约里的「仅自有」',
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_role_res_act (role_code, resource_type, action),
  INDEX idx_role (role_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='角色-资源-操作权限矩阵';

-- ----------------------------------------------------------------------------
-- 二、可信身份 DID 与密钥（需求 3.2 / 3.3）
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS did_identity (
  id             BIGINT PRIMARY KEY AUTO_INCREMENT,
  did            VARCHAR(128) NOT NULL UNIQUE COMMENT 'did:vpp:<type>:<hash>',
  subject_type   ENUM('user','device','org','edge') NOT NULL,
  subject_name   VARCHAR(128) NOT NULL,
  org_name       VARCHAR(128),
  controller_did VARCHAR(128),
  did_document   JSON NOT NULL,
  status         ENUM('active','frozen','revoked') NOT NULL DEFAULT 'active',
  metadata       JSON,
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_type_status (subject_type, status),
  INDEX idx_subject_name (subject_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='DID 身份主表';

CREATE TABLE IF NOT EXISTS did_key (
  id          BIGINT PRIMARY KEY AUTO_INCREMENT,
  did         VARCHAR(128) NOT NULL,
  algorithm   ENUM('SM2','ECC','RSA') NOT NULL DEFAULT 'SM2',
  public_key  VARCHAR(512) NOT NULL COMMENT '未压缩格式 04+X+Y',
  key_hash    VARCHAR(80)  NOT NULL COMMENT 'sm3:xxxx，公钥指纹',
  private_key_enc VARCHAR(1024) COMMENT '托管私钥，SM4-CBC 加密存储；非托管密钥为 NULL',
  custody     TINYINT(1) NOT NULL DEFAULT 0 COMMENT '1=平台托管 0=私钥仅在客户端',
  status      ENUM('active','frozen','revoked') NOT NULL DEFAULT 'active',
  version     INT NOT NULL DEFAULT 1 COMMENT '轮换次数',
  purpose     VARCHAR(32) NOT NULL DEFAULT 'sign' COMMENT 'sign|encrypt',
  bound_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expire_at   DATETIME,
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_did_status (did, status),
  INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='密钥表（私钥绝不明文落库）';

CREATE TABLE IF NOT EXISTS did_key_rotation_log (
  id             BIGINT PRIMARY KEY AUTO_INCREMENT,
  did            VARCHAR(128) NOT NULL,
  key_id         BIGINT       NOT NULL COMMENT '轮换后的新密钥 id',
  old_public_key VARCHAR(512),
  new_public_key VARCHAR(512) NOT NULL,
  old_version    INT NOT NULL DEFAULT 0,
  new_version    INT NOT NULL DEFAULT 1,
  reason         VARCHAR(255),
  operator_did   VARCHAR(128),
  evidence_id    VARCHAR(64),
  trace_id       VARCHAR(64),
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_did (did),
  INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='密钥轮换日志';

CREATE TABLE IF NOT EXISTS did_device_binding (
  id           BIGINT PRIMARY KEY AUTO_INCREMENT,
  did          VARCHAR(128) NOT NULL,
  device_id    VARCHAR(64)  NOT NULL COMMENT '对应 node_info.id',
  device_model VARCHAR(64),
  bound_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_online_at DATETIME,
  status       ENUM('bound','unbound') NOT NULL DEFAULT 'bound',
  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_device (device_id),
  INDEX idx_did (did)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='设备身份绑定关系';

-- ----------------------------------------------------------------------------
-- 三、能源数据资产（需求 3.4）
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS energy_asset (
  id           BIGINT PRIMARY KEY AUTO_INCREMENT,
  name         VARCHAR(255) NOT NULL,
  data_type    ENUM('pv','wind','storage','load','dispatch') NOT NULL,
  source_did   VARCHAR(128) NOT NULL COMMENT '数据来源主体 DID',
  owner_did    VARCHAR(128) COMMENT '数据归属主体，权限 scope=own 时按它判定',
  level        ENUM('L1','L2','L3','L4') NOT NULL DEFAULT 'L2' COMMENT 'L1公开 L2内部 L3敏感 L4核心',
  payload      JSON NOT NULL COMMENT '原始数据存 MySQL，链上只存摘要',
  payload_hash VARCHAR(80) NOT NULL COMMENT 'sm3:xxxx',
  description  VARCHAR(512),
  record_count INT NOT NULL DEFAULT 1,
  auth_status  ENUM('unauthorized','authorized','revoked') NOT NULL DEFAULT 'unauthorized',
  classify_score DECIMAL(5,3) COMMENT '算法服务给出的分级置信度',
  classify_reason VARCHAR(512),
  chain_tx_id  VARCHAR(64),
  evidence_id  VARCHAR(64),
  trace_id     VARCHAR(64),
  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_type_level (data_type, level),
  INDEX idx_source (source_did),
  INDEX idx_owner (owner_did),
  INDEX idx_created (created_at),
  INDEX idx_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='能源数据资产';

CREATE TABLE IF NOT EXISTS energy_asset_lineage (
  id          BIGINT PRIMARY KEY AUTO_INCREMENT,
  asset_id    BIGINT NOT NULL,
  stage       ENUM('register','authorize','access','compute','export') NOT NULL,
  actor_did   VARCHAR(128),
  evidence_id VARCHAR(64),
  hash        VARCHAR(80),
  trace_id    VARCHAR(64),
  detail      VARCHAR(512),
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_asset (asset_id, created_at),
  INDEX idx_trace (trace_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='资产溯源记录';

-- ----------------------------------------------------------------------------
-- 四、权限控制中心（需求 3.5）
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS perm_application (
  id             BIGINT PRIMARY KEY AUTO_INCREMENT,
  applicant_did  VARCHAR(128) NOT NULL,
  applicant_name VARCHAR(128),
  resource_type  ENUM('asset','model','dispatch','evidence','algo') NOT NULL,
  resource_id    VARCHAR(64) NOT NULL,
  action         ENUM('read','write','execute','issue','export') NOT NULL,
  reason         VARCHAR(512),
  status         ENUM('pending','approved','rejected','expired') NOT NULL DEFAULT 'pending',
  expire_at      DATETIME,
  approver_did   VARCHAR(128),
  approver_name  VARCHAR(128),
  approve_reason VARCHAR(512),
  approved_at    DATETIME,
  evidence_id    VARCHAR(64),
  trace_id       VARCHAR(64),
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_status (status, created_at),
  INDEX idx_applicant (applicant_did),
  INDEX idx_resource (resource_type, resource_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='权限申请';

CREATE TABLE IF NOT EXISTS perm_grant (
  id             BIGINT PRIMARY KEY AUTO_INCREMENT,
  application_id BIGINT,
  grantee_did    VARCHAR(128) NOT NULL,
  grantee_name   VARCHAR(128),
  resource_type  ENUM('asset','model','dispatch','evidence','algo') NOT NULL,
  resource_id    VARCHAR(64) NOT NULL,
  action         ENUM('read','write','execute','issue','export') NOT NULL,
  status         ENUM('active','revoked','expired') NOT NULL DEFAULT 'active',
  granted_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expire_at      DATETIME,
  revoked_at     DATETIME,
  revoke_reason  VARCHAR(255),
  evidence_id    VARCHAR(64),
  trace_id       VARCHAR(64),
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_grantee (grantee_did, status),
  INDEX idx_resource (resource_type, resource_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='已授权记录';

CREATE TABLE IF NOT EXISTS perm_change_log (
  id            BIGINT PRIMARY KEY AUTO_INCREMENT,
  target_did    VARCHAR(128) NOT NULL COMMENT '被变更权限的主体',
  change_type   ENUM('apply','approve','reject','grant','revoke','role_change','expire') NOT NULL,
  resource_type VARCHAR(32),
  resource_id   VARCHAR(64),
  action        VARCHAR(32),
  operator_did  VARCHAR(128),
  detail        VARCHAR(512),
  evidence_id   VARCHAR(64),
  trace_id      VARCHAR(64),
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_target_time (target_did, created_at),
  INDEX idx_type (change_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='权限变更留痕（供 R03 高频权限变更规则统计）';

-- 站内消息（铃铛）。按收件人 DID 扇出：一条事件发给 N 个人就是 N 行，
-- 因为「已读」是每个人各自的状态，不能共享一行。
CREATE TABLE IF NOT EXISTS sys_notice (
  id             BIGINT PRIMARY KEY AUTO_INCREMENT,
  recipient_did  VARCHAR(128) NOT NULL COMMENT '收件人 DID',
  recipient_name VARCHAR(128),
  category       VARCHAR(32)  NOT NULL COMMENT 'permission_apply/permission_result/permission_revoke',
  level          VARCHAR(16)  NOT NULL DEFAULT 'info' COMMENT 'info|success|warning',
  title          VARCHAR(128) NOT NULL,
  content        VARCHAR(512),
  link           VARCHAR(255) COMMENT '点击跳转的前端路由',
  ref_type       VARCHAR(32),
  ref_id         VARCHAR(64),
  actor_did      VARCHAR(128) COMMENT '触发者：申请人或审批人',
  actor_name     VARCHAR(128),
  status         VARCHAR(16)  NOT NULL DEFAULT 'unread' COMMENT 'unread|read',
  read_at        DATETIME,
  trace_id       VARCHAR(64),
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_recipient_status (recipient_did, status),
  INDEX idx_recipient_created (recipient_did, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='站内消息（铃铛）';

-- ----------------------------------------------------------------------------
-- 五、可信存证（需求 3.6）—— 本地哈希链
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS chain_evidence (
  id               BIGINT PRIMARY KEY AUTO_INCREMENT,
  evidence_id      VARCHAR(64) NOT NULL UNIQUE COMMENT 'ev-000457',
  category         ENUM('data','identity','permission','audit','algo') NOT NULL,
  ref_id           VARCHAR(64) NOT NULL COMMENT '业务对象ID',
  actor_did        VARCHAR(128),
  payload_hash     VARCHAR(80) NOT NULL COMMENT 'sm3:xxxx',
  prev_hash        VARCHAR(80) NOT NULL,
  block_hash       VARCHAR(80) NOT NULL COMMENT 'SM3(prev_hash + payload_hash + ts)',
  block_height     BIGINT NOT NULL,
  tx_id            VARCHAR(64) NOT NULL,
  trace_id         VARCHAR(64),
  payload_snapshot JSON COMMENT '仅用于完整性校验重算，链上概念中不存在',
  created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_height (block_height),
  INDEX idx_category_created (category, created_at),
  INDEX idx_actor (actor_did),
  INDEX idx_trace (trace_id),
  INDEX idx_ref (ref_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='存证记录（本地哈希链）';

-- B-001 / B-025：篡改演示的原始快照。原来只存 Redis（按 key 覆盖 + TTL），
-- 连续演示两次或 Redis 一清就再也还原不了，链永久停在 broken。
-- 落库后：同一条存证已有备份则不覆盖，restore 成功后删除该行。
CREATE TABLE IF NOT EXISTS chain_evidence_backup (
  id            BIGINT PRIMARY KEY AUTO_INCREMENT,
  evidence_id   VARCHAR(64) NOT NULL UNIQUE COMMENT '被篡改的存证 ID',
  payload_snapshot JSON NOT NULL COMMENT '篡改前的原始快照',
  payload_hash  VARCHAR(80) NOT NULL COMMENT '篡改前的 payload_hash',
  block_hash    VARCHAR(80) NOT NULL COMMENT '篡改前的 block_hash',
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='篡改演示的原始快照备份，restore 后删除';

-- ----------------------------------------------------------------------------
-- 六、安全审计（需求 3.7）—— 按月分表
-- 表名规则 audit_log_YYYYMM，后端写入前自动建当月表，跨月查询用 UNION ALL 合并
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS audit_log_202608 (
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='审计日志 2026-08';

-- 预建上个月与下个月的表，便于演示「跨月 UNION ALL 查询」
CREATE TABLE IF NOT EXISTS audit_log_202607 LIKE audit_log_202608;
CREATE TABLE IF NOT EXISTS audit_log_202609 LIKE audit_log_202608;

CREATE TABLE IF NOT EXISTS audit_alert (
  id         BIGINT PRIMARY KEY AUTO_INCREMENT,
  alert_id   VARCHAR(64) NOT NULL UNIQUE COMMENT 'al-000012',
  rule_code  VARCHAR(32) NOT NULL COMMENT 'R01_UNAUTHORIZED ~ R05_SUSPICIOUS_GRAD',
  rule_name  VARCHAR(64) NOT NULL,
  risk_level ENUM('low','medium','high','critical') NOT NULL DEFAULT 'high',
  message    VARCHAR(512) NOT NULL,
  actor_did  VARCHAR(128),
  actor_name VARCHAR(128),
  hit_count  INT NOT NULL DEFAULT 1 COMMENT '窗口内累计命中次数',
  trace_id   VARCHAR(64),
  status     ENUM('open','acked') NOT NULL DEFAULT 'open',
  acked_by   VARCHAR(128),
  acked_at   DATETIME,
  evidence_id VARCHAR(64),
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_status_time (status, created_at),
  INDEX idx_rule (rule_code),
  INDEX idx_actor (actor_did)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='风险告警';

-- ----------------------------------------------------------------------------
-- 七、算法业务表（需求 3.8 / 3.9 / 3.10）
-- 算法本身跑在 algo-service（乙负责），本库只落结果、指标与存证关联
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS algo_fl_task (
  id            BIGINT PRIMARY KEY AUTO_INCREMENT,
  task_id       VARCHAR(32) NOT NULL UNIQUE COMMENT 'fl-000012',
  name          VARCHAR(255) NOT NULL,
  node_ids      JSON NOT NULL,
  rounds        INT NOT NULL DEFAULT 10,
  current_round INT NOT NULL DEFAULT 0,
  status        ENUM('created','running','success','failed','cancelled') NOT NULL DEFAULT 'created',
  dp_enabled    TINYINT(1) NOT NULL DEFAULT 1,
  dp_epsilon    DECIMAL(8,4) DEFAULT 1.0000,
  dp_delta      DOUBLE DEFAULT 0.00001,
  dp_epsilon_spent DECIMAL(8,4) NOT NULL DEFAULT 0.0000,
  topk_enabled  TINYINT(1) NOT NULL DEFAULT 1,
  topk_ratio    DECIMAL(5,3) DEFAULT 0.100,
  compression_ratio DECIMAL(6,2) COMMENT '最新一轮压缩率 %',
  model_version VARCHAR(32),
  creator_did   VARCHAR(128),
  trace_id      VARCHAR(64),
  anomaly       JSON COMMENT 'B-029：算法服务上报的异常 {type,nodeId,round,detail}，gradient_poisoning / privacy_budget_exhausted / training_diverged',
  error         VARCHAR(512) COMMENT 'B-029：任务失败原因（算法服务 error 原文）',
  started_at    DATETIME,
  finished_at   DATETIME,
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_status (status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='联邦学习任务';

CREATE TABLE IF NOT EXISTS algo_fl_round (
  id                 BIGINT PRIMARY KEY AUTO_INCREMENT,
  task_id            VARCHAR(32) NOT NULL,
  round              INT NOT NULL,
  loss               DOUBLE COMMENT 'B-011：原 DECIMAL(10,6)，kW² 量级 MSE 会溢出导致整轮不落库不上链',
  acc                DECIMAL(6,4),
  compression_ratio  DECIMAL(6,2),
  epsilon_spent      DECIMAL(8,4),
  gradient_hash      VARCHAR(80) COMMENT '每轮梯度摘要，上链',
  node_contributions JSON,
  evidence_id        VARCHAR(64),
  created_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_task_round (task_id, round),
  INDEX idx_task (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='联邦学习每轮指标';

CREATE TABLE IF NOT EXISTS algo_model_version (
  id            BIGINT PRIMARY KEY AUTO_INCREMENT,
  version       VARCHAR(32) NOT NULL UNIQUE COMMENT 'v12',
  task_id       VARCHAR(32),
  name          VARCHAR(128),
  metrics       JSON COMMENT '{loss, acc, rounds}',
  status        ENUM('draft','published','deprecated') NOT NULL DEFAULT 'draft',
  publisher_did VARCHAR(128),
  published_at  DATETIME,
  model_hash    VARCHAR(80),
  evidence_id   VARCHAR(64),
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='模型版本';

CREATE TABLE IF NOT EXISTS algo_dispatch_task (
  id                BIGINT PRIMARY KEY AUTO_INCREMENT,
  task_id           VARCHAR(32) NOT NULL UNIQUE COMMENT 'dp-000009',
  name              VARCHAR(255) NOT NULL,
  time_window       VARCHAR(64),
  node_ids          JSON NOT NULL,
  status            ENUM('created','running','success','failed','cancelled') NOT NULL DEFAULT 'created',
  strategy          JSON COMMENT '{actions, totalReward, timeWindow}',
  total_reward      DECIMAL(10,3),
  q_table           JSON,
  explanation       TEXT COMMENT 'DeepSeek 生成的策略解释',
  explanation_source ENUM('live','cache','rule') DEFAULT 'rule',
  issued            TINYINT(1) NOT NULL DEFAULT 0,
  command_id        VARCHAR(32),
  signer_did        VARCHAR(128),
  signature         VARCHAR(256),
  issued_at         DATETIME,
  ack_status        ENUM('none','partial','all') NOT NULL DEFAULT 'none',
  ack_detail        JSON,
  creator_did       VARCHAR(128),
  evidence_id       VARCHAR(64),
  trace_id          VARCHAR(64),
  created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_status (status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='智能调度任务';

CREATE TABLE IF NOT EXISTS algo_ai_analysis (
  id          BIGINT PRIMARY KEY AUTO_INCREMENT,
  scene       ENUM('dispatch','risk','data','qa','audit') NOT NULL,
  context     JSON,
  question    VARCHAR(1024),
  answer      TEXT,
  reasoning   JSON,
  source      ENUM('live','cache','rule') NOT NULL DEFAULT 'rule' COMMENT '三级降级标记',
  latency_ms  INT,
  actor_did   VARCHAR(128),
  evidence_id VARCHAR(64),
  trace_id    VARCHAR(64),
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_scene_time (scene, created_at),
  INDEX idx_actor (actor_did)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='AI 智能分析记录';

-- 注：本表为契约表清单外的补充表，用于支撑 GET /risk/history。
-- 只有甲使用，乙不直连数据库，因此不影响任何跨人员接口。
CREATE TABLE IF NOT EXISTS algo_risk_assessment (
  id          BIGINT PRIMARY KEY AUTO_INCREMENT,
  node_id     VARCHAR(32) NOT NULL,
  risk_score  DECIMAL(6,2) NOT NULL,
  level       ENUM('low','medium','high','critical') NOT NULL,
  features    JSON,
  factors     JSON,
  suggestion  VARCHAR(512),
  actor_did   VARCHAR(128),
  evidence_id VARCHAR(64),
  trace_id    VARCHAR(64),
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_node_time (node_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='隐私风险评估历史';

-- ----------------------------------------------------------------------------
-- 八、节点与指标（支撑现有前端页面，字段与 raspi/public/data/mock.json 对齐）
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS node_info (
  id             VARCHAR(32) PRIMARY KEY COMMENT 'Node-A',
  name           VARCHAR(64)  NOT NULL,
  status         ENUM('online','warning','offline') NOT NULL DEFAULT 'offline',
  model          VARCHAR(32)  NOT NULL COMMENT 'VPP-2000',
  did            VARCHAR(128),
  location       VARCHAR(128),
  capacity_kw    DECIMAL(10,2),
  pv_output      DECIMAL(10,2) NOT NULL DEFAULT 0 COMMENT '当前光伏出力 kW',
  storage_output DECIMAL(10,2) NOT NULL DEFAULT 0 COMMENT '当前储能出力 kW，负值为充电',
  load_kw        DECIMAL(10,2) NOT NULL DEFAULT 0 COMMENT '当前负荷 kW，接口字段名为 load',
  soc            DECIMAL(6,2)  NOT NULL DEFAULT 0 COMMENT '荷电状态 %',
  last_seen_at   DATETIME,
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_status (status),
  INDEX idx_did (did)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='节点信息';

CREATE TABLE IF NOT EXISTS node_metric (
  id             BIGINT PRIMARY KEY AUTO_INCREMENT,
  node_id        VARCHAR(32) NOT NULL,
  ts             DATETIME NOT NULL COMMENT '采样时刻',
  pv_output      DECIMAL(10,2) NOT NULL DEFAULT 0,
  storage_output DECIMAL(10,2) NOT NULL DEFAULT 0,
  load_kw        DECIMAL(10,2) NOT NULL DEFAULT 0,
  soc            DECIMAL(6,2)  NOT NULL DEFAULT 0,
  price          DECIMAL(6,3)  COMMENT '分时电价 元/kWh',
  UNIQUE KEY uk_node_ts (node_id, ts),
  INDEX idx_ts (ts)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='节点历史指标（供曲线图）';
